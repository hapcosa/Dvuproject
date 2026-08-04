"""Emite el catálogo en PDF con el diseño del impreso.

**Nada de esto hay que ir a buscarlo al PDF cada vez.** El extractor de Fase 0 ya sacó
las tres piezas y `cargar-catalogo --con-imagenes` las dejó en el almacén:

- las fotos de producto (`producto.imagen_key`),
- el logo de la marca (`producto.marca_logo_key`) — en el impreso la marca es un PNG del
  proveedor, no texto, y por eso `producto.marca` está casi siempre vacío,
- la banda roja del encabezado y las páginas de arte (`catalogo_activo`,
  `catalogo_pagina`).

La geometría sale de `extractor.layout.RANGOS_X`, los rangos de X medidos sobre el PDF
real: el impreso se lee con esos números y se vuelve a emitir con los mismos, así que
recalibrar una edición nueva se hace en un solo lugar.

Qué es idéntico y qué no. La banda del encabezado, la portada, las páginas de oferta y
la contraportada **son las originales**: se copian del PDF de imprenta, no se redibujan.
El cuerpo se rearma desde la base, porque ahí está el precio de hoy y las correcciones
que el PDF no tiene; lo que se conserva es la maqueta —siete columnas en el mismo orden
y proporción, encabezado azul, grilla roja— y no el salto de página del original, que
depende de cuántos productos haya.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import BaseDocTemplate, Frame, Image, LongTable, PageTemplate, Spacer
from reportlab.platypus import Paragraph as Parrafo
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from dvu.almacenamiento import Almacen
from dvu.db.models import CatalogoActivo, CatalogoPagina, Categoria, Producto
from dvu.extractor.layout import RANGOS_X

#: Muestreados sobre el PDF de imprenta, no elegidos a ojo: la grilla es #ED1C23 y la
#: fila de encabezados #2D58A7.
ROJO_BORDE = HexColor("#ed1c23")
AZUL = HexColor("#2d58a7")
AZUL_OSCURO = HexColor("#1d3f7d")
ROJO = HexColor("#c8102e")
GRIS = HexColor("#9ca3af")
BLANCO = HexColor("#ffffff")

ANCHO, ALTO = A4
MARGEN = 8.0
#: Espacio entre la banda y la tabla, y alto del pie de página.
AIRE = 4.0
PIE = 16.0
#: Alto de la banda si no hay ninguna guardada y hay que dibujarla a mano.
BANDA_DE_RESPALDO = 46.0

#: La foto ocupa el alto de la fila del impreso menos el aire de la celda.
FOTO_ANCHO = 62.0
FOTO_ALTO = 46.0
#: El logo de marca es una tira ancha y baja: en el original mide 52 × 18 pt.
LOGO_ANCHO = 52.0
LOGO_ALTO = 18.0

#: Claves de `catalogo_activo` con la banda del encabezado, según la página sea par o
#: impar. El impreso espeja el logo como cualquier pliego y acá se hace igual.
CLAVE_BANNER = {0: "banner_par", 1: "banner_impar"}

ENCABEZADOS = {
    "codigo": "Código",
    "imagen": "Imagen",
    "descripcion": "Descripción",
    "venta_min": "Detalle<br/>Venta Min",
    "marca": "Marca",
    "medida": "Medida",
    "precio": "Precio",
}

_ORDEN: tuple[str, ...] = tuple(ENCABEZADOS)


def _columnas(con_imagenes: bool) -> tuple[str, ...]:
    """Sin fotos la columna «Imagen» no se deja vacía, se saca.

    Una columna en blanco a lo largo de 116 páginas no informa nada y le roba ancho a la
    descripción, que es lo que el vendedor necesita leer en la lista de precios.
    """
    return _ORDEN if con_imagenes else tuple(c for c in _ORDEN if c != "imagen")


def _anchos_de_columna(columnas: tuple[str, ...]) -> list[float]:
    """Reescala los rangos del impreso al ancho útil de la página.

    Los rangos van de 10 a 595 pt sobre el A4 completo; acá hay que meterlos entre los
    márgenes. Se escalan proporcionalmente para no deformar la proporción entre columnas,
    que es lo que hace que la página se lea como la del catálogo.
    """
    crudos = [RANGOS_X[col][1] - RANGOS_X[col][0] for col in columnas]  # type: ignore[index]
    util = ANCHO - 2 * MARGEN
    factor = util / sum(crudos)
    return [ancho * factor for ancho in crudos]


_ESTILO_CELDA = ParagraphStyle(
    "celda",
    fontName="Helvetica",
    fontSize=7.5,
    leading=8.5,
    alignment=TA_CENTER,
)
_ESTILO_CODIGO = ParagraphStyle("codigo", parent=_ESTILO_CELDA, fontName="Courier", fontSize=7)
_ESTILO_DESC = ParagraphStyle("desc", parent=_ESTILO_CELDA, fontSize=7.5)
_ESTILO_PRECIO = ParagraphStyle("precio", parent=_ESTILO_CELDA, fontName="Helvetica-Bold")
_ESTILO_ENCABEZADO = ParagraphStyle(
    "encabezado",
    parent=_ESTILO_CELDA,
    fontName="Helvetica",
    fontSize=8.5,
    leading=10,
    textColor=BLANCO,
)


def formatear_clp(monto: int) -> str:
    """`12990` -> `$ 12.990`. Sin decimales: el peso chileno no los tiene."""
    return "$ " + f"{monto:,}".replace(",", ".")


def _vacio() -> Parrafo:
    """Dato que el catálogo no trae. Se marca, no se inventa."""
    return Parrafo('<font color="#9ca3af">—</font>', _ESTILO_CELDA)


def _escapar(texto: str) -> str:
    """Las celdas van en el mini-HTML de reportlab: un `&` o un `<` del catálogo
    («PERNO 1/2 X 3 <ACERO>») rompería el párrafo entero."""
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _venta_minima(producto: Producto) -> str:
    """Mismo texto que muestra la web: `12 X UNID`, `200 X BOLSA`.

    Vacío cuando el producto se vende suelto y sin envase declarado: en el impreso esa
    celda también está en blanco, y es un dato faltante real, no un error de parseo.
    """
    if producto.multiplo_venta <= 1 and not producto.envase:
        return ""
    envase = producto.envase or producto.unidad_venta or "UNID"
    return _escapar(f"{producto.multiplo_venta} X {envase}")


@dataclass(frozen=True, slots=True)
class Portada:
    """Rótulos de la banda cuando no hay una guardada y hay que dibujarla."""

    titulo: str = "CATALOGO"
    subtitulo: str = "FERRETERIA"


@dataclass(slots=True)
class Banda:
    """La banda roja original, ya bajada del almacén."""

    #: Ruta al PNG en el directorio temporal de la corrida.
    ruta: Path
    alto: float
    #: De qué lado está el logo DVU. El folio va del lado contrario, como en el impreso.
    logo_a_la_izquierda: bool


class _Documento(BaseDocTemplate):  # type: ignore[misc]  # reportlab no trae stubs
    """Pone la banda del encabezado y el pie en cada página; la tabla va en el marco."""

    def __init__(
        self, destino: Any, portada: Portada, bandas: dict[int, Banda], *, folio_inicial: int = 1
    ) -> None:
        self._portada = portada
        self._bandas = bandas
        #: Cuántas páginas de arte van pegadas antes del cuerpo. Sin esto el folio y el
        #: espejado de la banda irían corridos respecto de la página física.
        self._corrimiento = folio_inicial - 1
        alto_banda = max((b.alto for b in bandas.values()), default=BANDA_DE_RESPALDO)
        super().__init__(
            destino,
            pagesize=A4,
            leftMargin=MARGEN,
            rightMargin=MARGEN,
            topMargin=MARGEN,
            bottomMargin=MARGEN,
            title="Catálogo Comercial DVU SpA",
            author="Comercial DVU SpA",
        )
        marco = Frame(
            MARGEN,
            MARGEN + PIE,
            ANCHO - 2 * MARGEN,
            ALTO - 2 * MARGEN - alto_banda - AIRE - PIE,
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
            id="cuerpo",
        )
        self.addPageTemplates([PageTemplate(id="catalogo", frames=[marco], onPage=self._adorno)])

    def _adorno(self, canvas: Canvas, doc: BaseDocTemplate) -> None:
        canvas.saveState()
        folio = canvas.getPageNumber() + self._corrimiento
        ancho = ANCHO - 2 * MARGEN
        banda = self._bandas.get(folio % 2) or next(iter(self._bandas.values()), None)

        if banda is None:
            self._banda_de_respaldo(canvas, ancho)
            y = ALTO - MARGEN - BANDA_DE_RESPALDO
            alto = BANDA_DE_RESPALDO
            folio_a_la_derecha = True
        else:
            alto = banda.alto
            y = ALTO - MARGEN - alto
            canvas.drawImage(str(banda.ruta), MARGEN, y, width=ancho, height=alto, mask="auto")
            folio_a_la_derecha = banda.logo_a_la_izquierda

        canvas.setFillColor(BLANCO)
        canvas.setFont("Helvetica", 11)
        x = ANCHO - MARGEN - 18 if folio_a_la_derecha else MARGEN + 12
        canvas.drawCentredString(x, y + alto - 20, str(folio))

        canvas.setFillColor(AZUL)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(
            ANCHO / 2,
            MARGEN + 5,
            "Precios netos en pesos, no incluyen IVA. Venta por múltiplos de la venta mínima.",
        )
        canvas.restoreState()

    def _banda_de_respaldo(self, canvas: Canvas, ancho: float) -> None:
        """Sin banda guardada el catálogo sale igual, con una franja lisa.

        Pasa cuando se carga el catálogo sin almacén, o antes de la primera extracción de
        plantilla. Preferimos una franja pobre a un PDF que no se puede generar.
        """
        y = ALTO - MARGEN - BANDA_DE_RESPALDO
        canvas.setFillColor(ROJO)
        canvas.rect(MARGEN, y, ancho, BANDA_DE_RESPALDO, fill=1, stroke=0)
        canvas.setFillColor(BLANCO)
        canvas.setFont("Helvetica", 20)
        canvas.drawCentredString(ANCHO / 2, y + BANDA_DE_RESPALDO - 26, self._portada.titulo)
        canvas.setFont("Helvetica", 8)
        canvas.drawCentredString(ANCHO / 2, y + 8, self._portada.subtitulo)


def _consultar(
    session: Session, *, categoria: str | None, q: str | None, limite: int | None
) -> list[Producto]:
    consulta = select(Producto).where(Producto.activo.is_(True))
    if categoria:
        consulta = consulta.where(
            Producto.categoria_id.in_(select(Categoria.id).where(Categoria.slug == categoria))
        )
    if q:
        consulta = consulta.where(Producto.descripcion.ilike(f"%{q.strip()}%"))
    consulta = consulta.options(selectinload(Producto.alias)).order_by(
        Producto.descripcion, Producto.sku
    )
    if limite is not None:
        consulta = consulta.limit(limite)
    return list(session.scalars(consulta))


def _bajar(keys: Iterable[str | None], almacen: Almacen, destino: Path) -> dict[str, Path]:
    """Baja cada objeto **una sola vez** y devuelve key -> ruta en disco.

    Las keys son el hash del archivo, así que una misma foto sirve a toda una familia de
    productos y un mismo logo a cientos de filas: sin este caché se bajarían decenas de
    veces y el PDF pesaría varias veces lo necesario.

    Lo que no está o no se puede decodificar se omite. Se verifica acá y no al dibujar
    porque reportlab abre el archivo en medio del armado de la página: un JPEG truncado
    tumbaría el catálogo entero por una sola foto mala, y son ~900.
    """
    rutas: dict[str, Path] = {}
    for key in keys:
        if key is None or key in rutas:
            continue
        datos = almacen.leer(key)
        if datos is None or not _es_imagen_legible(datos):
            continue
        ruta = destino / key.replace("/", "_")
        ruta.write_bytes(datos)
        rutas[key] = ruta
    return rutas


def _es_imagen_legible(datos: bytes) -> bool:
    from PIL import Image as PilImage

    try:
        with PilImage.open(BytesIO(datos)) as img:
            img.load()
    except Exception:
        return False
    return True


def _bajar_bandas(session: Session, almacen: Almacen, destino: Path) -> dict[int, Banda]:
    """Las dos versiones de la banda, indexadas por paridad de página."""
    from PIL import Image as PilImage

    activos = {a.clave: a.key_objeto for a in session.scalars(select(CatalogoActivo))}
    bandas: dict[int, Banda] = {}
    for paridad, clave in CLAVE_BANNER.items():
        key = activos.get(clave)
        if key is None:
            continue
        rutas = _bajar([key], almacen, destino)
        ruta = rutas.get(key)
        if ruta is None:
            continue
        with PilImage.open(ruta) as img:
            proporcion = img.height / img.width
            izquierda = _logo_a_la_izquierda(img)
        bandas[paridad] = Banda(
            ruta=ruta,
            alto=(ANCHO - 2 * MARGEN) * proporcion,
            logo_a_la_izquierda=izquierda,
        )
    return bandas


def _logo_a_la_izquierda(imagen: Any) -> bool:
    """¿De qué lado está el logo? Del lado donde la banda deja de ser roja.

    El logo DVU es azul y blanco sobre el degradado rojo, así que la mitad con más
    píxeles no rojos es la que lo tiene. Se mide en vez de fijarlo porque las dos
    variantes salen de páginas cualesquiera del PDF y no siempre en el mismo orden.
    """
    chica = imagen.convert("RGB").resize((80, 20))
    izquierda = derecha = 0
    for x in range(80):
        for y in range(20):
            rojo, verde, azul = chica.getpixel((x, y))
            if rojo > azul + 30 and rojo > verde + 30:
                continue
            if x < 40:
                izquierda += 1
            else:
                derecha += 1
    return izquierda > derecha


def _celda_imagen(producto: Producto, rutas: dict[str, Path]) -> Any:
    ruta = rutas.get(producto.imagen_key or "")
    if ruta is None:
        # El espaciador mantiene el alto de la fila aunque falte la foto: en el impreso
        # todas las filas miden lo mismo, y una fila enana en medio de la página delata
        # el hueco más de lo que lo disimula.
        return [_vacio(), Spacer(1, FOTO_ALTO - 12)]
    # `lazy=2` deja que reportlab abra el archivo al dibujar y lo suelte: con 900 fotos
    # la diferencia entre eso y tenerlas todas en memoria son cientos de MB.
    return Image(str(ruta), width=FOTO_ANCHO, height=FOTO_ALTO, kind="proportional", lazy=2)


def _celda_marca(producto: Producto, logos: dict[str, Path]) -> Any:
    """El logo del proveedor. Si no está, el nombre; si tampoco, se marca el hueco."""
    ruta = logos.get(producto.marca_logo_key or "")
    if ruta is not None:
        return Image(str(ruta), width=LOGO_ANCHO, height=LOGO_ALTO, kind="proportional", lazy=2)
    if producto.marca:
        return Parrafo(_escapar(producto.marca), _ESTILO_CELDA)
    return _vacio()


def _filas(
    productos: list[Producto],
    rutas: dict[str, Path],
    logos: dict[str, Path],
    columnas: tuple[str, ...],
) -> Iterator[list[Any]]:
    yield [Parrafo(ENCABEZADOS[col], _ESTILO_ENCABEZADO) for col in columnas]
    for producto in productos:
        codigos = [alias.codigo for alias in producto.alias] or [producto.sku]
        venta = _venta_minima(producto)
        celdas: dict[str, Any] = {
            "codigo": Parrafo("<br/>".join(_escapar(c) for c in codigos), _ESTILO_CODIGO),
            "imagen": _celda_imagen(producto, rutas),
            "descripcion": Parrafo(_escapar(producto.descripcion.upper()), _ESTILO_DESC),
            "venta_min": Parrafo(venta, _ESTILO_CELDA) if venta else _vacio(),
            "marca": _celda_marca(producto, logos),
            "medida": Parrafo(_escapar(producto.medida), _ESTILO_CELDA)
            if producto.medida
            else _vacio(),
            "precio": Parrafo(formatear_clp(int(producto.precio_lista_clp)), _ESTILO_PRECIO),
        }
        yield [celdas[col] for col in columnas]


def _estilo_tabla() -> list[tuple[Any, ...]]:
    return [
        ("GRID", (0, 0), (-1, -1), 0.75, ROJO_BORDE),
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("BOX", (0, 0), (-1, 0), 0.8, AZUL_OSCURO),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
    ]


def _paginas_de_diseno(session: Session) -> tuple[list[CatalogoPagina], list[CatalogoPagina]]:
    """Las páginas de arte activas, partidas en las que van antes y después del cuerpo.

    En el original las ofertas están intercaladas entre las tablas, pero acá la
    paginación la decide cuántos productos hay: no existe «la página 53» a la que
    volverlas. Se conserva el orden relativo — portada y lanzamiento adelante, ofertas
    después del cuerpo, contraportada al final — que es lo que el lector reconoce.
    """
    paginas = list(
        session.scalars(
            select(CatalogoPagina)
            .where(CatalogoPagina.activa.is_(True))
            .order_by(CatalogoPagina.archivo, CatalogoPagina.pagina)
        )
    )
    adelante = [p for p in paginas if p.tipo == "portada" or p.pagina <= 2]
    atras = [p for p in paginas if p not in adelante and p.tipo != "contraportada"]
    atras += [p for p in paginas if p.tipo == "contraportada"]
    return adelante, atras


def _bajar_paginas(paginas: list[CatalogoPagina], almacen: Almacen) -> list[bytes]:
    """El PDF de cada página de arte. Las que faltan en el almacén se saltean: un
    catálogo sin su portada sigue sirviendo; sin precios, no."""
    return [datos for p in paginas if (datos := almacen.leer(p.key_pdf))]


def _armar(cuerpo: bytes, adelante: list[bytes], atras: list[bytes]) -> bytes:
    """Pega las páginas originales alrededor del cuerpo generado.

    Se copian como PDF, no como imagen: mantienen el vector, el texto y el peso del
    original.
    """
    import fitz  # PyMuPDF

    documento = fitz.open()
    for pdf in [*adelante, cuerpo, *atras]:
        with fitz.open(stream=pdf, filetype="pdf") as origen:
            documento.insert_pdf(origen)

    # Las páginas de arte traen las fuentes y los objetos del original, y varias repiten
    # el mismo fondo: sin recolectar y desduplicar, 37 páginas suman 70 MB y el catálogo
    # no se puede ni mandar por correo.
    salida: bytes = documento.tobytes(garbage=4, deflate=True)
    documento.close()
    return salida


def _contar_paginas(pdfs: list[bytes]) -> int:
    import fitz  # PyMuPDF

    total = 0
    for pdf in pdfs:
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            total += doc.page_count
    return total


def exportar_catalogo_pdf(
    session: Session,
    almacen: Almacen,
    *,
    categoria: str | None = None,
    q: str | None = None,
    con_imagenes: bool = True,
    con_diseno: bool | None = None,
    limite: int | None = None,
    portada: Portada | None = None,
) -> bytes:
    """Devuelve el catálogo en PDF, listo para imprimir o mandar por WhatsApp.

    `con_imagenes=False` da una lista de precios: se genera en segundos y pesa cien veces
    menos, que es lo que sirve cuando lo que se quiere es el precio actualizado y no el
    catálogo ilustrado.

    `con_diseno` decide si se pegan la portada, las ofertas y la contraportada
    originales. Por defecto acompaña a `con_imagenes`: una lista de precios con portada
    de catálogo confunde sobre lo que es.
    """
    productos = _consultar(session, categoria=categoria, q=q, limite=limite)
    buffer = BytesIO()
    columnas = _columnas(con_imagenes)
    diseno = con_imagenes if con_diseno is None else con_diseno

    with tempfile.TemporaryDirectory(prefix="dvu-catalogo-") as tmp:
        temporal = Path(tmp)
        rutas = _bajar((p.imagen_key for p in productos), almacen, temporal) if con_imagenes else {}
        logos = _bajar((p.marca_logo_key for p in productos), almacen, temporal)
        bandas = _bajar_bandas(session, almacen, temporal) if diseno else {}

        # Las páginas de arte se bajan antes del cuerpo porque su cantidad decide en qué
        # número arranca la numeración —y con ella de qué lado va el logo de la banda—.
        adelante_p, atras_p = _paginas_de_diseno(session) if diseno else ([], [])
        adelante = _bajar_paginas(adelante_p, almacen)
        atras = _bajar_paginas(atras_p, almacen)

        tabla = LongTable(
            list(_filas(productos, rutas, logos, columnas)),
            colWidths=_anchos_de_columna(columnas),
            # El encabezado azul se repite en cada página, como en el impreso: una hoja
            # suelta del catálogo tiene que poder leerse sola.
            repeatRows=1,
            style=_estilo_tabla(),
        )
        documento = _Documento(
            buffer,
            portada or Portada(),
            bandas,
            folio_inicial=_contar_paginas(adelante) + 1,
        )
        documento.build([tabla])

        cuerpo = buffer.getvalue()
        if not diseno:
            return cuerpo
        return _armar(cuerpo, adelante, atras)


__all__ = ["Banda", "Portada", "exportar_catalogo_pdf", "formatear_clp"]
