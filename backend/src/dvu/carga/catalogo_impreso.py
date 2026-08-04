"""Exporta el catálogo a PDF con el diseño del impreso.

**Las fotos no hay que ir a buscarlas**: el extractor de Fase 0 ya las sacó del PDF
original (`extractor/imagenes.py`, JPEG embebidos a ~300 ppi, deduplicados por sha256) y
`cargar-catalogo --con-imagenes` las dejó en el almacén con su `imagen_key`. Este módulo
las vuelve a bajar de ahí, no del PDF.

La geometría sale de `extractor.layout.RANGOS_X`, que son los rangos de X medidos sobre
el PDF real. O sea: el impreso se lee con esos números y se vuelve a emitir con los
mismos. Si el catálogo de la próxima edición mueve una columna, se recalibra en un solo
lugar y las dos puntas quedan consistentes.

Esto **no** es una copia página por página del original. El original salió de CorelDRAW,
con saltos de sección y portadas armadas a mano; acá el contenido es lo que hay en la
base —que ya incluye correcciones que el PDF no tiene— y la paginación la decide el
contenido. Lo que se conserva es la identidad visual: la banda roja, las siete columnas
en el mismo orden y ancho, el encabezado azul y los bordes rojos de las celdas.
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
from dvu.db.models import Categoria, Producto
from dvu.extractor.layout import RANGOS_X

# Los mismos colores que `dvu.css`, que a su vez están calcados del impreso.
ROJO = HexColor("#c8102e")
ROJO_OSCURO = HexColor("#7d0a1c")
ROJO_BORDE = HexColor("#d21f33")
AZUL = HexColor("#1a4f9c")
AZUL_OSCURO = HexColor("#123a75")
GRIS = HexColor("#9ca3af")

ANCHO, ALTO = A4
MARGEN = 10.0
#: Alto de la banda roja del encabezado, en puntos.
BANDA = 46.0
#: Espacio bajo la banda antes de la tabla.
AIRE = 6.0
PIE = 18.0

#: La foto ocupa el alto de la fila del impreso menos el aire de la celda.
FOTO_ANCHO = 62.0
FOTO_ALTO = 46.0

ENCABEZADOS = {
    "codigo": "Código",
    "imagen": "Imagen",
    "descripcion": "Descripción",
    "venta_min": "Venta Min",
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
    fontName="Helvetica-Bold",
    fontSize=8.5,
    leading=10,
    textColor=HexColor("#ffffff"),
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
    """Lo que va en la banda roja de cada página."""

    titulo: str = "CATÁLOGO DVU"
    subtitulo: str = "COMERCIAL DVU SpA"


class _Documento(BaseDocTemplate):  # type: ignore[misc]  # reportlab no trae stubs
    """Dibuja la banda roja y el pie en cada página; la tabla va dentro del marco."""

    def __init__(self, destino: Any, portada: Portada) -> None:
        super().__init__(
            destino,
            pagesize=A4,
            leftMargin=MARGEN,
            rightMargin=MARGEN,
            topMargin=MARGEN,
            bottomMargin=MARGEN,
            title=portada.titulo,
            author="Comercial DVU SpA",
        )
        self._portada = portada
        marco = Frame(
            MARGEN,
            MARGEN + PIE,
            ANCHO - 2 * MARGEN,
            ALTO - 2 * MARGEN - BANDA - AIRE - PIE,
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
            id="cuerpo",
        )
        self.addPageTemplates([PageTemplate(id="catalogo", frames=[marco], onPage=self._adorno)])

    def _adorno(self, canvas: Canvas, doc: BaseDocTemplate) -> None:
        canvas.saveState()
        y = ALTO - MARGEN - BANDA
        ancho = ANCHO - 2 * MARGEN

        canvas.setFillColor(ROJO)
        canvas.setStrokeColor(ROJO_OSCURO)
        canvas.setLineWidth(1.5)
        canvas.rect(MARGEN, y, ancho, BANDA, fill=1, stroke=1)

        canvas.setFillColor(HexColor("#ffffff"))
        canvas.setFont("Helvetica-Bold", 20)
        canvas.drawCentredString(ANCHO / 2, y + BANDA - 24, self._portada.titulo)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawCentredString(ANCHO / 2, y + 9, self._portada.subtitulo)

        # El folio, igual que en el impreso: arriba a la izquierda de la banda.
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(MARGEN + 8, y + BANDA - 16, str(canvas.getPageNumber()))

        canvas.setFillColor(AZUL)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(
            ANCHO / 2,
            MARGEN + 6,
            "Precios netos en pesos, no incluyen IVA. Venta por múltiplos de la venta mínima.",
        )
        canvas.restoreState()


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


def _bajar_fotos(productos: Iterable[Producto], almacen: Almacen, destino: Path) -> dict[str, Path]:
    """Baja cada foto **una sola vez** y devuelve key -> ruta en disco.

    Las keys del extractor son el hash del archivo, así que una misma foto sirve a toda
    una familia de productos: sin este caché se bajaría decenas de veces y el PDF
    resultante pesaría varias veces lo necesario.
    """
    rutas: dict[str, Path] = {}
    for producto in productos:
        key = producto.imagen_key
        if key is None or key in rutas:
            continue
        datos = almacen.leer(key)
        if datos is None or not _es_imagen_legible(datos):
            # Foto que no está o que no se puede decodificar: la fila va sin imagen. Se
            # verifica acá y no al dibujar porque reportlab abre el archivo en medio del
            # armado de la página: un JPEG truncado tumbaría el catálogo entero por una
            # sola foto mala, y son ~900.
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


def _filas(
    productos: list[Producto], rutas: dict[str, Path], columnas: tuple[str, ...]
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
            "marca": Parrafo(_escapar(producto.marca), _ESTILO_CELDA)
            if producto.marca
            else _vacio(),
            "medida": Parrafo(_escapar(producto.medida), _ESTILO_CELDA)
            if producto.medida
            else _vacio(),
            "precio": Parrafo(formatear_clp(int(producto.precio_lista_clp)), _ESTILO_PRECIO),
        }
        yield [celdas[col] for col in columnas]


def _estilo_tabla() -> list[tuple[Any, ...]]:
    return [
        ("GRID", (0, 0), (-1, -1), 0.5, ROJO_BORDE),
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("BOX", (0, 0), (-1, 0), 0.8, AZUL_OSCURO),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        # Cebra tenue: la fila del impreso mide ~78 pt y sin ella el ojo salta de línea.
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#ffffff"), HexColor("#fff7f8")]),
    ]


def exportar_catalogo_pdf(
    session: Session,
    almacen: Almacen,
    *,
    categoria: str | None = None,
    q: str | None = None,
    con_imagenes: bool = True,
    limite: int | None = None,
    portada: Portada | None = None,
) -> bytes:
    """Devuelve el catálogo en PDF, listo para imprimir o mandar por WhatsApp.

    `con_imagenes=False` da una lista de precios: se genera en segundos y pesa cien veces
    menos, que es lo que sirve cuando lo que se quiere es el precio actualizado y no el
    catálogo ilustrado.
    """
    productos = _consultar(session, categoria=categoria, q=q, limite=limite)
    buffer = BytesIO()

    columnas = _columnas(con_imagenes)

    with tempfile.TemporaryDirectory(prefix="dvu-catalogo-") as tmp:
        rutas = _bajar_fotos(productos, almacen, Path(tmp)) if con_imagenes else {}
        tabla = LongTable(
            list(_filas(productos, rutas, columnas)),
            colWidths=_anchos_de_columna(columnas),
            # El encabezado azul se repite en cada página, como en el impreso: una hoja
            # suelta del catálogo tiene que poder leerse sola.
            repeatRows=1,
            style=_estilo_tabla(),
        )
        documento = _Documento(buffer, portada or Portada())
        documento.build([tabla])

    return buffer.getvalue()


__all__ = ["Portada", "exportar_catalogo_pdf", "formatear_clp"]
