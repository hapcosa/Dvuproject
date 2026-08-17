"""Carga del catálogo extraído (Fase 0) hacia `producto` / `producto_alias`.

Principios:

- **Idempotente**: correr la carga dos veces con el mismo JSONL deja la base igual.
  Se identifica la corrida por el sha256 del PDF; se reemplazan sus filas crudas.
- **Nada se borra**: los productos que dejan de aparecer en una edición del catálogo
  se marcan `activo=False`, no se eliminan — pueden estar en pedidos históricos.
- **El código del proveedor no es el SKU**: `PR/49573` queda en `producto_alias`
  (para que el vendedor busque por lo que tiene a mano) y el producto vive como
  `DVU-PR49573`.
- Las filas de `revision.jsonl` **también** se guardan como fila cruda: son la lista
  de trabajo pendiente, no basura.

Sobre los campos `*_raw` de `catalogo_fila_cruda`: el extractor sólo preserva el texto
literal de la celda en `venta_min` y `medida`. En el resto se guarda el valor
normalizado, que es reversible en la práctica (`normalizar_codigo` y el uppercase de
descripción/marca no pierden información de negocio).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.almacenamiento import Almacen
from dvu.db import maqueta
from dvu.db.models import (
    CatalogoActivo,
    CatalogoFilaCruda,
    CatalogoFuente,
    CatalogoPagina,
    Producto,
    ProductoAlias,
)

#: De dónde viene el código alternativo. Hoy sólo el catálogo impreso; en Fase 2
#: convivirá con los códigos de cada proveedor.
ORIGEN_ALIAS = "catalogo_pdf"

#: `extraer --con-imagenes` deja las fotos acá y el mapa fila -> foto al lado.
SUBDIR_IMAGENES = "imagenes"
ARCHIVO_IMAGENES = "imagenes.json"

#: `extraer` deja acá los activos del diseño: banda del encabezado, logos de marca y
#: las páginas de arte recortadas del PDF original.
SUBDIR_PLANTILLA = "plantilla"
ARCHIVO_PLANTILLA = "plantilla.json"

#: Extensión del archivo -> tipo MIME. PyMuPDF devuelve casi siempre `jpeg`.
TIPOS_POR_EXTENSION = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "pdf": "application/pdf",
}


@dataclass(slots=True)
class ResumenCarga:
    fuentes: int = 0
    filas_crudas: int = 0
    productos_creados: int = 0
    productos_actualizados: int = 0
    alias_creados: int = 0
    desactivados: int = 0
    imagenes_subidas: int = 0
    productos_con_imagen: int = 0
    logos_subidos: int = 0
    productos_con_marca: int = 0
    paginas_diseno: int = 0
    #: Códigos repetidos dentro del mismo JSONL cuyos datos no coinciden. Gana el
    #: primero y el conflicto se reporta: es un error del catálogo, no de la carga.
    conflictos: list[str] = field(default_factory=list)

    def resumen(self) -> str:
        lineas = [
            "",
            "═══ Carga de catálogo ═══",
            f"  Fuentes registradas    : {self.fuentes}",
            f"  Filas crudas guardadas : {self.filas_crudas}",
            f"  Productos creados      : {self.productos_creados}",
            f"  Productos actualizados : {self.productos_actualizados}",
            f"  Alias creados          : {self.alias_creados}",
            f"  Imágenes subidas       : {self.imagenes_subidas}",
            f"  Productos con imagen   : {self.productos_con_imagen}",
            f"  Logos de marca subidos : {self.logos_subidos}",
            f"  Productos con marca    : {self.productos_con_marca}",
            f"  Páginas de diseño      : {self.paginas_diseno}",
            f"  Productos desactivados : {self.desactivados}",
            f"  Códigos en conflicto   : {len(self.conflictos)}",
        ]
        lineas += [f"    {c}" for c in self.conflictos[:20]]
        if len(self.conflictos) > 20:
            lineas.append(f"    … y {len(self.conflictos) - 20} más")
        return "\n".join([*lineas, ""])


def _leer_jsonl(ruta: Path) -> Iterator[dict[str, Any]]:
    if not ruta.exists():
        return
    with ruta.open(encoding="utf-8") as fh:
        for linea in fh:
            if linea.strip():
                yield json.loads(linea)


def cargar_catalogo(
    session: Session,
    directorio: Path,
    *,
    desactivar_ausentes: bool = False,
    almacen: Almacen | None = None,
) -> ResumenCarga:
    """Carga `catalogo.jsonl` + `revision.jsonl` + `fuentes.json` de `directorio`.

    `desactivar_ausentes` marca `activo=False` todo producto que no venga en este
    JSONL. Sólo tiene sentido cuando se carga el catálogo **completo**; por eso está
    apagado por defecto.

    Con `almacen`, además sube las fotos que dejó `extraer --con-imagenes` y les pone el
    `imagen_key` a los productos. Sin él la carga es exactamente la de antes: la
    extracción de imágenes es una corrida aparte y muy lenta, así que cargar el texto no
    puede depender de que exista.
    """
    resumen = ResumenCarga()

    fuentes = _registrar_fuentes(session, directorio, resumen)
    session.flush()

    cargables = list(_leer_jsonl(directorio / "catalogo.jsonl"))
    revision = list(_leer_jsonl(directorio / "revision.jsonl"))
    _guardar_filas_crudas(session, [*cargables, *revision], fuentes, resumen)

    imagenes = _subir_imagenes(directorio, almacen, resumen) if almacen else {}
    marcas = _cargar_plantilla(session, directorio, almacen, resumen) if almacen else {}
    vistos = _upsert_productos(session, cargables, resumen, imagenes, marcas)

    if desactivar_ausentes:
        resumen.desactivados = _desactivar_ausentes(session, vistos)

    session.flush()
    return resumen


def _registrar_fuentes(
    session: Session, directorio: Path, resumen: ResumenCarga
) -> dict[str, CatalogoFuente]:
    """Upsert por sha256. Recargar el mismo PDF reemplaza sus filas crudas."""
    ruta = directorio / "fuentes.json"
    if not ruta.exists():
        raise FileNotFoundError(f"Falta {ruta}: corre `dvu extraer` antes de cargar")

    fuentes: dict[str, CatalogoFuente] = {}
    for meta in json.loads(ruta.read_text(encoding="utf-8")):
        fuente = session.scalar(
            select(CatalogoFuente).where(CatalogoFuente.sha256 == meta["sha256"])
        )
        if fuente is None:
            fuente = CatalogoFuente(archivo=meta["archivo"], sha256=meta["sha256"])
            session.add(fuente)
        else:
            # Recarga: las filas crudas anteriores de esta fuente ya no valen.
            session.query(CatalogoFilaCruda).filter_by(fuente_id=fuente.id).delete()

        fuente.paginas = meta["paginas"]
        fuente.filas_detectadas = meta["filas_detectadas"]
        fuente.filas_cargables = meta["filas_cargables"]
        fuentes[meta["archivo"]] = fuente
        resumen.fuentes += 1

    return fuentes


def _guardar_filas_crudas(
    session: Session,
    registros: list[dict[str, Any]],
    fuentes: dict[str, CatalogoFuente],
    resumen: ResumenCarga,
) -> None:
    for reg in registros:
        fuente = fuentes.get(reg["archivo"])
        if fuente is None:
            continue
        session.add(
            CatalogoFilaCruda(
                fuente_id=fuente.id,
                pagina=reg["pagina"],
                orden=reg["orden"],
                codigo_raw=reg["codigo"],
                descripcion_raw=reg["descripcion"],
                venta_min_raw=reg["venta_minima"]["raw"],
                marca_raw=reg["marca"],
                medida_raw=reg["medida"]["texto"] or None,
                precio_raw=str(reg["precio_clp"]) if reg["precio_clp"] else None,
                confianza=Decimal(str(reg["confianza"])),
                problemas=list(reg["problemas"]),
                posicion={"pagina": reg["pagina"], "orden": reg["orden"]},
            )
        )
        resumen.filas_crudas += 1


def _subir_imagenes(
    directorio: Path, almacen: Almacen, resumen: ResumenCarga
) -> dict[str, dict[str, str]]:
    """Sube las fotos extraídas al almacén y devuelve el mapa archivo -> «pág:orden» -> key.

    Cada key se sube **una sola vez** aunque la compartan veinte filas: el extractor ya
    dedupe por sha256, y una foto sirve a toda una familia de productos.

    Una foto que falta en disco no aborta la carga: se omite del mapa y esa fila queda
    sin imagen. El catálogo con una foto menos sirve; sin precios, no.
    """
    ruta = directorio / ARCHIVO_IMAGENES
    if not ruta.exists():
        return {}

    mapa: dict[str, dict[str, str]] = json.loads(ruta.read_text(encoding="utf-8"))
    dir_imagenes = directorio / SUBDIR_IMAGENES
    subidas: set[str] = set()

    for asociaciones in mapa.values():
        for posicion, key in list(asociaciones.items()):
            if key in subidas:
                continue
            origen = dir_imagenes / Path(key).name
            tipo = TIPOS_POR_EXTENSION.get(origen.suffix.lstrip(".").lower())
            if not origen.exists() or tipo is None:
                del asociaciones[posicion]
                continue
            with origen.open("rb") as fh:
                almacen.guardar(key, fh, tipo)
            subidas.add(key)
            resumen.imagenes_subidas += 1

    return mapa


def _cargar_plantilla(
    session: Session, directorio: Path, almacen: Almacen, resumen: ResumenCarga
) -> dict[str, dict[str, str]]:
    """Sube los activos del diseño y registra páginas de arte y banda del encabezado.

    Devuelve el mapa archivo -> «pág:orden» -> key del logo de marca, que `_upsert`
    aplica a cada producto.
    """
    ruta = directorio / ARCHIVO_PLANTILLA
    if not ruta.exists():
        return {}

    datos: dict[str, dict[str, Any]] = json.loads(ruta.read_text(encoding="utf-8"))
    origen = directorio / SUBDIR_PLANTILLA
    subidas: set[str] = set()

    def subir(key: str) -> bool:
        """`False` si el archivo no está: se omite y el catálogo sale sin esa pieza."""
        if key in subidas:
            return True
        archivo = origen / Path(key).name
        tipo = TIPOS_POR_EXTENSION.get(archivo.suffix.lstrip(".").lower())
        if not archivo.exists() or tipo is None:
            return False
        with archivo.open("rb") as fh:
            almacen.guardar(key, fh, tipo)
        subidas.add(key)
        return True

    activos = {a.clave: a for a in session.scalars(select(CatalogoActivo))}
    paginas = {(p.archivo, p.pagina): p for p in session.scalars(select(CatalogoPagina))}
    marcas: dict[str, dict[str, str]] = {}

    for archivo, bloque in datos.items():
        for paridad, key in bloque.get("banners", {}).items():
            if not subir(key):
                continue
            clave = f"banner_{paridad}"
            activo = activos.get(clave)
            if activo is None:
                activo = CatalogoActivo(clave=clave, key_objeto=key)
                session.add(activo)
                activos[clave] = activo
            activo.key_objeto = key

        for info in bloque.get("paginas", []):
            if not (subir(info["key_pdf"]) and subir(info["key_png"])):
                continue
            pagina = paginas.get((archivo, info["pagina"]))
            if pagina is None:
                pagina = CatalogoPagina(archivo=archivo, pagina=info["pagina"])
                session.add(pagina)
                paginas[(archivo, info["pagina"])] = pagina
            pagina.tipo = info["tipo"]
            pagina.key_pdf = info["key_pdf"]
            pagina.key_png = info["key_png"]
            resumen.paginas_diseno += 1

        asignaciones = dict(bloque.get("marcas", {}))
        for posicion, key in list(asignaciones.items()):
            if not subir(key):
                del asignaciones[posicion]
        marcas[archivo] = asignaciones

    # Las páginas recién creadas entran con `orden = 0`: el extractor sabe de qué página
    # del PDF salió cada una, no en qué lugar de la maqueta la quiere el administrador.
    maqueta.numerar_faltantes(session)

    resumen.logos_subidos = sum(1 for k in subidas if k.startswith("catalogo/marcas/"))
    return marcas


def _upsert_productos(
    session: Session,
    registros: list[dict[str, Any]],
    resumen: ResumenCarga,
    imagenes: dict[str, dict[str, str]],
    marcas: dict[str, dict[str, str]],
) -> set[str]:
    """Devuelve los SKU vistos en esta carga."""
    existentes = {p.sku: p for p in session.scalars(select(Producto))}
    alias_existentes = {(a.codigo, a.origen) for a in session.scalars(select(ProductoAlias))}
    vistos: set[str] = set()

    for reg in registros:
        sku = reg["sku"]
        if sku in vistos:
            if _difiere(existentes[sku], reg):
                resumen.conflictos.append(f"{reg['codigo']} (pág. {reg['pagina']})")
            continue
        vistos.add(sku)

        producto = existentes.get(sku)
        if producto is None:
            producto = Producto(sku=sku)
            session.add(producto)
            existentes[sku] = producto
            resumen.productos_creados += 1
        else:
            resumen.productos_actualizados += 1

        _aplicar(producto, reg)

        # Una foto que ya no viene en esta edición no se borra: mejor la del catálogo
        # anterior que un hueco en la grilla.
        key = imagenes.get(reg["archivo"], {}).get(f"{reg['pagina']}:{reg['orden']}")
        if key is not None:
            producto.imagen_key = key
            resumen.productos_con_imagen += 1

        logo = marcas.get(reg["archivo"], {}).get(f"{reg['pagina']}:{reg['orden']}")
        if logo is not None:
            producto.marca_logo_key = logo
            resumen.productos_con_marca += 1

        clave = (reg["codigo"], ORIGEN_ALIAS)
        if clave not in alias_existentes:
            producto.alias.append(ProductoAlias(codigo=reg["codigo"], origen=ORIGEN_ALIAS))
            alias_existentes.add(clave)
            resumen.alias_creados += 1

    return vistos


def _aplicar(producto: Producto, reg: dict[str, Any]) -> None:
    venta = reg["venta_minima"]
    medida = reg["medida"]

    producto.descripcion = reg["descripcion"]
    producto.marca = reg["marca"]
    producto.medida = medida["texto"] or None
    producto.medida_valor = Decimal(medida["valor"]) if medida["valor"] is not None else None
    producto.medida_unidad = medida["unidad"]
    producto.unidad_venta = venta["unidad"] or "UNID"
    # El múltiplo nunca se inventa: si el catálogo no lo dice, se vende de a 1 y la
    # fila queda marcada en `problemas` para que alguien la revise.
    producto.multiplo_venta = max(1, venta["multiplo"] or 1)
    producto.envase = venta["envase"]
    producto.precio_lista_clp = Decimal(reg["precio_clp"])
    producto.activo = True


def _difiere(producto: Producto, reg: dict[str, Any]) -> bool:
    return producto.descripcion != reg["descripcion"] or producto.precio_lista_clp != Decimal(
        reg["precio_clp"]
    )


def _desactivar_ausentes(session: Session, vistos: set[str]) -> int:
    n = 0
    for producto in session.scalars(select(Producto).where(Producto.activo.is_(True))):
        if producto.sku not in vistos:
            producto.activo = False
            n += 1
    return n
