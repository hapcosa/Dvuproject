"""Carga del catálogo extraído hacia la base.

Los datos de ejemplo son filas reales del catálogo (páginas 3, 56 y 71), incluida una
con `venta_min` crudo larguísimo por una celda mal fusionada: `catalogo_fila_cruda` no
puede rechazarla, es justamente la que hay que poder diagnosticar después.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, BinaryIO

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dvu.carga.catalogo import ORIGEN_ALIAS, cargar_catalogo
from dvu.db.models import CatalogoFilaCruda, CatalogoFuente, Producto, ProductoAlias

pytestmark = pytest.mark.integration

ARCHIVO = "CAT ACT 10 JULIO 2026 PARTE 1.pdf"
SHA = "a" * 64


def _fila(
    codigo: str,
    descripcion: str,
    precio: int | None,
    *,
    pagina: int = 3,
    orden: int = 0,
    multiplo: int | None = 12,
    unidad: str | None = "UNID",
    envase: str | None = None,
    venta_raw: str | None = "X 12 UNID",
    medida: str = "",
    marca: str | None = None,
) -> dict[str, Any]:
    return {
        "archivo": ARCHIVO,
        "pagina": pagina,
        "orden": orden,
        "codigo": codigo,
        "sku": f"DVU-{codigo.replace('/', '')}",
        "descripcion": descripcion,
        "marca": marca,
        "precio_clp": precio,
        "venta_minima": {
            "multiplo": multiplo,
            "unidad": unidad,
            "envase": envase,
            "raw": venta_raw,
        },
        "medida": {"texto": medida, "valor": None, "unidad": None},
        "confianza": 1.0,
        "problemas": [],
    }


def _escribir(
    directorio: Path, cargables: list[dict[str, Any]], revision: list[dict[str, Any]]
) -> None:
    for nombre, filas in (("catalogo.jsonl", cargables), ("revision.jsonl", revision)):
        (directorio / nombre).write_text(
            "".join(json.dumps(f, ensure_ascii=False) + "\n" for f in filas), encoding="utf-8"
        )
    (directorio / "fuentes.json").write_text(
        json.dumps(
            [
                {
                    "archivo": ARCHIVO,
                    "sha256": SHA,
                    "paginas": 75,
                    "filas_detectadas": len(cargables) + len(revision),
                    "filas_cargables": len(cargables),
                }
            ]
        ),
        encoding="utf-8",
    )


@pytest.fixture
def extraccion(tmp_path: Path) -> Path:
    cargables = [
        _fila("PR/49573", "LIQUIDO DE FRENO FEDERAL", 1790, orden=0),
        _fila(
            "H20184",
            "UNIÓN DOBLE PLANSA PP",
            114,
            pagina=56,
            orden=4,
            multiplo=100,
            unidad="UN",
            venta_raw="X 100 UN",
            medida='1/2"',
        ),
        _fila(
            "H20185",
            "UNIÓN DOBLE PLANSA PP",
            287,
            pagina=56,
            orden=5,
            multiplo=50,
            unidad="UN",
            venta_raw="X 50 UN",
            medida='3/4"',
        ),
    ]
    revision = [
        # Sin precio: no es producto vendible, pero su rastro se guarda igual.
        _fila(
            "SINPRECIO1",
            "",
            None,
            pagina=71,
            orden=2,
            multiplo=None,
            unidad=None,
            venta_raw="1.000 UN " * 11,
        )
    ]
    _escribir(tmp_path, cargables, revision)
    return tmp_path


def test_carga_crea_productos_alias_y_filas_crudas(sesion: Session, extraccion: Path) -> None:
    resumen = cargar_catalogo(sesion, extraccion)

    assert resumen.fuentes == 1
    assert resumen.productos_creados == 3
    assert resumen.alias_creados == 3
    # Las filas de revisión también se guardan: son la lista de trabajo pendiente.
    assert resumen.filas_crudas == 4
    assert sesion.scalar(select(func.count()).select_from(Producto)) == 3

    producto = sesion.scalar(select(Producto).where(Producto.sku == "DVU-H20185"))
    assert producto is not None
    assert producto.descripcion == "UNIÓN DOBLE PLANSA PP"
    assert producto.precio_lista_clp == 287
    assert producto.multiplo_venta == 50
    assert producto.medida == '3/4"'
    assert producto.activo is True


def test_el_codigo_del_proveedor_queda_como_alias_no_como_sku(
    sesion: Session, extraccion: Path
) -> None:
    cargar_catalogo(sesion, extraccion)

    alias = sesion.scalar(select(ProductoAlias).where(ProductoAlias.codigo == "PR/49573"))
    assert alias is not None
    assert alias.origen == ORIGEN_ALIAS
    assert alias.producto.sku == "DVU-PR49573"


def test_fila_cruda_acepta_texto_largo_de_celda_mal_fusionada(
    sesion: Session, extraccion: Path
) -> None:
    cargar_catalogo(sesion, extraccion)

    cruda = sesion.scalar(
        select(CatalogoFilaCruda).where(CatalogoFilaCruda.codigo_raw == "SINPRECIO1")
    )
    assert cruda is not None
    assert cruda.venta_min_raw is not None
    assert len(cruda.venta_min_raw) > 64
    assert cruda.precio_raw is None


def test_recargar_es_idempotente(sesion: Session, extraccion: Path) -> None:
    cargar_catalogo(sesion, extraccion)
    sesion.flush()
    segunda = cargar_catalogo(sesion, extraccion)

    assert segunda.productos_creados == 0
    assert segunda.productos_actualizados == 3
    assert segunda.alias_creados == 0
    assert sesion.scalar(select(func.count()).select_from(Producto)) == 3
    assert sesion.scalar(select(func.count()).select_from(CatalogoFilaCruda)) == 4
    assert sesion.scalar(select(func.count()).select_from(CatalogoFuente)) == 1


def test_codigo_duplicado_con_datos_distintos_se_reporta_y_gana_el_primero(
    sesion: Session, tmp_path: Path
) -> None:
    # Caso real: el código 105101 aparece en las páginas 8 y 9 con distinta redacción.
    _escribir(
        tmp_path,
        [
            _fila("105101", "CERRADURA RUSTICA B60 ACCESO PRINCIPAL POMO-POMO", 40631, pagina=8),
            _fila("105101", "CERRADURA ACCESO RUSTICA B60", 40631, pagina=9, orden=1),
        ],
        [],
    )

    resumen = cargar_catalogo(sesion, tmp_path)

    assert resumen.productos_creados == 1
    assert resumen.conflictos == ["105101 (pág. 9)"]
    producto = sesion.scalar(select(Producto).where(Producto.sku == "DVU-105101"))
    assert producto is not None
    assert producto.descripcion.startswith("CERRADURA RUSTICA B60")


def test_desactivar_ausentes_no_borra_productos(sesion: Session, extraccion: Path) -> None:
    cargar_catalogo(sesion, extraccion)
    sesion.flush()

    # Nueva edición del catálogo: sólo queda uno de los tres productos.
    _escribir(extraccion, [_fila("PR/49573", "LIQUIDO DE FRENO FEDERAL", 1790)], [])
    resumen = cargar_catalogo(sesion, extraccion, desactivar_ausentes=True)

    assert resumen.desactivados == 2
    # Siguen existiendo: pueden estar referenciados en pedidos históricos.
    assert sesion.scalar(select(func.count()).select_from(Producto)) == 3
    inactivo = sesion.scalar(select(Producto).where(Producto.sku == "DVU-H20184"))
    assert inactivo is not None
    assert inactivo.activo is False


def test_falta_fuentes_json_es_error_explicito(sesion: Session, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="dvu extraer"):
        cargar_catalogo(sesion, tmp_path)


@pytest.fixture
def con_imagenes(extraccion: Path) -> Path:
    """Añade a la extracción una foto que sirve a las dos filas de la página 56.

    Es el caso real: el catálogo trae una foto por familia de productos, no por SKU.
    """
    (extraccion / "imagenes").mkdir()
    (extraccion / "imagenes" / "abc123.jpg").write_bytes(b"\xff\xd8\xff datos jpeg")
    (extraccion / "imagenes.json").write_text(
        json.dumps({ARCHIVO: {"56:4": "catalogo/abc123.jpg", "56:5": "catalogo/abc123.jpg"}}),
        encoding="utf-8",
    )
    return extraccion


class AlmacenEspia:
    """Almacén de mentira: registra lo que se subió sin tocar disco ni MinIO."""

    def __init__(self) -> None:
        self.subidos: list[tuple[str, str]] = []
        self.contenido: dict[str, bytes] = {}

    def guardar(self, key: str, datos: BinaryIO, content_type: str) -> str:
        self.subidos.append((key, content_type))
        self.contenido[key] = datos.read()
        return key

    def url_firmada(self, key: str, *, segundos: int = 300) -> str:
        return f"https://ejemplo/{key}"

    def leer(self, key: str) -> bytes | None:
        return self.contenido.get(key)


def test_las_fotos_se_suben_una_vez_y_se_asocian_a_cada_fila(
    sesion: Session, con_imagenes: Path
) -> None:
    almacen = AlmacenEspia()

    resumen = cargar_catalogo(sesion, con_imagenes, almacen=almacen)

    # Una sola subida aunque la compartan dos filas: el extractor ya deduplicó por sha.
    assert almacen.subidos == [("catalogo/abc123.jpg", "image/jpeg")]
    assert resumen.imagenes_subidas == 1
    assert resumen.productos_con_imagen == 2

    for sku in ("DVU-H20184", "DVU-H20185"):
        producto = sesion.scalar(select(Producto).where(Producto.sku == sku))
        assert producto is not None
        assert producto.imagen_key == "catalogo/abc123.jpg"


def test_sin_almacen_la_carga_ignora_las_imagenes(sesion: Session, con_imagenes: Path) -> None:
    """Cargar el texto no puede depender de la extracción de fotos, que son horas."""
    resumen = cargar_catalogo(sesion, con_imagenes)

    assert resumen.imagenes_subidas == 0
    producto = sesion.scalar(select(Producto).where(Producto.sku == "DVU-H20184"))
    assert producto is not None
    assert producto.imagen_key is None


def test_una_foto_que_falta_en_disco_no_aborta_la_carga(
    sesion: Session, con_imagenes: Path
) -> None:
    """El catálogo con una foto menos sirve; sin precios, no."""
    (con_imagenes / "imagenes" / "abc123.jpg").unlink()
    almacen = AlmacenEspia()

    resumen = cargar_catalogo(sesion, con_imagenes, almacen=almacen)

    assert almacen.subidos == []
    assert resumen.productos_creados == 3
    producto = sesion.scalar(select(Producto).where(Producto.sku == "DVU-H20184"))
    assert producto is not None
    assert producto.imagen_key is None


def test_recargar_sin_imagenes_no_borra_la_foto_que_ya_estaba(
    sesion: Session, con_imagenes: Path
) -> None:
    """Una edición nueva del PDF sin fotos deja la del catálogo anterior: mejor esa que
    un hueco en la grilla."""
    cargar_catalogo(sesion, con_imagenes, almacen=AlmacenEspia())
    sesion.flush()
    (con_imagenes / "imagenes.json").unlink()

    cargar_catalogo(sesion, con_imagenes)

    producto = sesion.scalar(select(Producto).where(Producto.sku == "DVU-H20184"))
    assert producto is not None
    assert producto.imagen_key == "catalogo/abc123.jpg"
