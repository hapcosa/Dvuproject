"""Los activos gráficos del catálogo impreso: banda superior, logos de marca y páginas
enteras de diseño (portada, contraportada y promociones).

El catálogo no es sólo una tabla. Tres cosas del PDF original no son texto y sin ellas
lo que se emite no es el catálogo de DVU sino una planilla con sus datos:

1. **La banda roja de cada página.** Es una imagen embebida a sangre (610×74 px) con el
   logo DVU y el rótulo "CATALOGO FERRETERIA". Alterna de lado según la página sea par
   o impar, como en cualquier pliego impreso.
2. **La marca.** En la columna «Marca» casi no hay texto: hay un PNG del logo del
   proveedor. Por eso el extractor reporta 1.929 filas `sin_marca` — no faltan datos,
   están en una imagen. `extractor.imagenes` los descartaba a propósito por chicos.
3. **Las páginas de diseño.** Portada, la página de lanzamiento de producto y las de
   oferta intercaladas en el cuerpo. No tienen filas; son arte de página completa.
   Se copian **verbatim** del PDF original: redibujarlas sería una imitación peor.

Todo se identifica por geometría, no por nombre de archivo: los PDF salen de CorelDRAW
y no traen metadatos aprovechables.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import fitz  # PyMuPDF

from dvu.extractor.layout import RANGOS_X

#: La banda superior ocupa casi todo el ancho de la página y vive arriba de todo.
BANNER_ANCHO_MINIMO = 400.0
BANNER_Y_MAXIMO = 130.0

#: Resolución de la banda. A 200 ppi el logo aguanta el zoom en pantalla y la franja
#: pesa ~80 kB, que se paga una vez para todo el catálogo.
PPI_BANNER = 200

#: Franja donde se busca la banda, en puntos desde el borde superior de la página.
#: Debajo de esto ya empieza la fila azul de encabezados.
ALTO_BUSQUEDA_BANNER = 150.0

#: Un logo de marca es chico y ancho. Los umbrales dejan afuera las viñetas de la
#: columna «Imagen» que a veces se desbordan, y a los separadores de 26×23 px.
MARCA_ANCHO_MINIMO_PX = 60
MARCA_BYTES_MINIMO = 1500

#: Resolución de la vista previa de las páginas de diseño para la web. 110 ppi deja
#: una A4 en ~900 px de ancho: se ve nítida en pantalla y pesa pocos cientos de kB.
PPI_VISTA_PREVIA = 110


@dataclass(slots=True)
class LogoMarca:
    pagina: int
    #: `catalogo/marcas/<sha256[:16]>.<ext>`. Un mismo logo se repite en cientos de
    #: filas: el hash lo deja en un solo archivo.
    key: str
    ruta: Path
    top: float
    bottom: float

    @property
    def centro_y(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass(slots=True)
class PaginaDiseno:
    """Una página que es arte, no tabla: portada, promoción o contraportada."""

    archivo: str
    pagina: int
    tipo: str
    #: PDF de una sola página, recortado del original. Es lo que se reinserta al emitir.
    key_pdf: str
    ruta_pdf: Path
    #: PNG para mostrarla en la web sin un visor de PDF.
    key_png: str
    ruta_png: Path


def extraer_banners(pdf: Path, destino: Path, paginas_con_filas: set[int]) -> dict[str, str]:
    """Las dos versiones de la banda: logo a la izquierda y logo a la derecha.

    El catálogo está armado como pliego, así que el logo se espeja según la página sea
    par o impar. Se guarda una de cada una y el generador alterna igual que el original.
    Devuelve `{"par": key, "impar": key}`; si sólo aparece una variante, ambas apuntan
    a la misma y el catálogo sale con el logo siempre del mismo lado.
    """
    banners: dict[str, str] = {}
    for numero in sorted(paginas_con_filas):
        paridad = "par" if numero % 2 == 0 else "impar"
        if paridad in banners:
            continue
        key = _banner_de_pagina(pdf, destino, numero)
        if key is not None:
            banners[paridad] = key
        if len(banners) == 2:
            break

    if len(banners) == 1:
        unica = next(iter(banners.values()))
        banners = {"par": unica, "impar": unica}
    return banners


def _banner_de_pagina(pdf: Path, destino: Path, numero: int) -> str | None:
    """Guarda la banda roja del encabezado, limpia, y devuelve su key.

    No sirve sacar la imagen embebida: la franja es un degradado sobre el que van, en
    vectores, el logo DVU y el rótulo "CATALOGO / FERRETERIA". Lo que se guarda es el
    **rasterizado de la zona**, que trae las tres capas ya compuestas.

    Antes de rasterizar se borra el número de página, que también vive dentro de la
    banda: se elimina el texto con una redacción sin relleno, así que el degradado que
    tiene debajo queda intacto y no hay que parchar ningún rectángulo.

    Sólo se miran páginas de tabla: la portada y las de oferta también tienen manchas
    rojas grandes arriba —el sello "NUEVO PRODUCTO", por ejemplo— y ganarían la carrera.
    """
    destino.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf) as doc:
        if not 1 <= numero <= doc.page_count:
            return None
        caja = _caja_de_la_banda(doc[numero - 1])
        if caja is None:
            return None

        # Sobre una copia: `apply_redactions` modifica el documento.
        copia = fitz.open()
        copia.insert_pdf(doc, from_page=numero - 1, to_page=numero - 1)
        pagina = copia[0]
        for palabra in pagina.get_text("words"):
            x0, y0, x1, y1 = palabra[:4]
            if caja.y0 <= (y0 + y1) / 2 <= caja.y1:
                pagina.add_redact_annot(fitz.Rect(x0 - 1, y0 - 1, x1 + 1, y1 + 1))
        pagina.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

        datos: bytes = pagina.get_pixmap(clip=caja, dpi=PPI_BANNER).tobytes("png")
        copia.close()
        return _volcar(datos, "png", destino, "catalogo/plantilla")


def _caja_de_la_banda(pagina: fitz.Page) -> fitz.Rect | None:
    """Rectángulo exacto de la franja roja, medido sobre los píxeles.

    La banda no está a la misma altura en todas las páginas (30.1, 33.0 y 40.2 pt en
    tres páginas cualesquiera): el pliego se armó a mano. Por eso se busca dónde la
    página se pone roja en vez de confiar en una coordenada fija.
    """
    caja = fitz.Rect(0, 0, pagina.rect.width, ALTO_BUSQUEDA_BANNER)
    mapa = pagina.get_pixmap(clip=caja, dpi=100)
    escala = mapa.height / ALTO_BUSQUEDA_BANNER

    def es_roja(y: int) -> bool:
        muestras = [mapa.pixel(x, y) for x in range(0, mapa.width, 40)]
        return sum(1 for p in muestras if _es_rojo(p)) > len(muestras) * 0.6

    # La primera racha continua de filas rojas. No sirve min/max: la línea de la grilla
    # que va debajo del encabezado azul también es roja, y estiraría la banda hasta ahí.
    racha = _primera_racha(es_roja, mapa.height)
    if racha is None:
        return None
    y0, y1 = racha

    # Bordes laterales: la banda está insertada unos 8 pt respecto del papel.
    medio = (y0 + y1) // 2
    columnas = [x for x in range(mapa.width) if _es_rojo(mapa.pixel(x, medio))]
    if not columnas:
        return None
    return fitz.Rect(
        min(columnas) / escala, y0 / escala, (max(columnas) + 1) / escala, (y1 + 1) / escala
    )


def _primera_racha(predicado: Callable[[int], bool], alto: int) -> tuple[int, int] | None:
    inicio: int | None = None
    for y in range(alto):
        if predicado(y):
            if inicio is None:
                inicio = y
        elif inicio is not None:
            return inicio, y - 1
    return (inicio, alto - 1) if inicio is not None else None


def _es_rojo(pixel: tuple[int, ...]) -> bool:
    rojo, verde, azul = pixel[0], pixel[1], pixel[2]
    return rojo > azul + 30 and rojo > verde + 30


def logo_a_la_izquierda(banda: bytes) -> bool:
    """¿De qué lado de la banda está el logo DVU? Del lado donde deja de ser roja.

    El logo es azul y blanco sobre el degradado, así que la mitad con más píxeles no
    rojos es la que lo tiene. Se mide en vez de fijarlo porque las dos variantes se
    recortan de páginas cualesquiera y no siempre salen en el mismo orden.

    Lo usan el generador de PDF y la web para poner el folio del lado contrario, que es
    donde va en el impreso.
    """
    from PIL import Image as PilImage

    with PilImage.open(BytesIO(banda)) as imagen:
        chica = imagen.convert("RGB").resize((80, 20))

    # Los bytes vienen por filas, tres por píxel: el índice módulo el ancho da la columna.
    crudo = chica.tobytes()
    izquierda = derecha = 0
    for indice in range(80 * 20):
        if _es_rojo(tuple(crudo[indice * 3 : indice * 3 + 3])):
            continue
        if indice % 80 < 40:
            izquierda += 1
        else:
            derecha += 1
    return izquierda > derecha


def extraer_marcas(pdf: Path, destino: Path, *, hasta_pagina: int | None = None) -> list[LogoMarca]:
    """Vuelca los logos de la columna «Marca» y devuelve dónde cae cada uno."""
    destino.mkdir(parents=True, exist_ok=True)
    x0_col, x1_col = RANGOS_X["marca"]
    encontrados: list[LogoMarca] = []

    with fitz.open(pdf) as doc:
        fin = min(hasta_pagina or doc.page_count, doc.page_count)
        for numero in range(1, fin + 1):
            pagina = doc[numero - 1]
            vistos: set[tuple[int, int]] = set()
            for info in pagina.get_images(full=True):
                xref = info[0]
                base = doc.extract_image(xref)
                datos: bytes = base["image"]
                if base["width"] < MARCA_ANCHO_MINIMO_PX or len(datos) < MARCA_BYTES_MINIMO:
                    continue

                for rect in pagina.get_image_rects(xref):
                    if not x0_col <= (rect.x0 + rect.x1) / 2 < x1_col:
                        continue
                    # El mismo logo se declara varias veces en la página, en la misma
                    # posición: PyMuPDF los devuelve repetidos y duplicarían la fila.
                    clave = (int(rect.y0), xref)
                    if clave in vistos:
                        continue
                    vistos.add(clave)

                    key, ruta = _volcar_con_ruta(
                        datos, base.get("ext", "png"), destino, "catalogo/marcas"
                    )
                    encontrados.append(
                        LogoMarca(
                            pagina=numero,
                            key=key,
                            ruta=ruta,
                            top=float(rect.y0),
                            bottom=float(rect.y1),
                        )
                    )
    return encontrados


def extraer_paginas_diseno(
    pdf: Path, destino: Path, paginas_con_filas: set[int], *, hasta_pagina: int | None = None
) -> list[PaginaDiseno]:
    """Recorta las páginas sin filas de producto: son arte de página completa.

    `paginas_con_filas` viene de la extracción de texto. Una página que no produjo
    ninguna fila o es diseño o es una tabla que el extractor no supo leer; en ambos
    casos conviene guardarla tal cual, porque así al menos se ve en el catálogo.
    """
    destino.mkdir(parents=True, exist_ok=True)
    resultado: list[PaginaDiseno] = []

    with fitz.open(pdf) as doc:
        fin = min(hasta_pagina or doc.page_count, doc.page_count)
        for numero in range(1, fin + 1):
            if numero in paginas_con_filas:
                continue

            recorte = fitz.open()
            recorte.insert_pdf(doc, from_page=numero - 1, to_page=numero - 1)
            datos_pdf: bytes = recorte.tobytes()
            recorte.close()
            key_pdf, ruta_pdf = _volcar_con_ruta(datos_pdf, "pdf", destino, "catalogo/paginas")

            datos_png: bytes = doc[numero - 1].get_pixmap(dpi=PPI_VISTA_PREVIA).tobytes("png")
            key_png, ruta_png = _volcar_con_ruta(datos_png, "png", destino, "catalogo/paginas")

            resultado.append(
                PaginaDiseno(
                    archivo=pdf.name,
                    pagina=numero,
                    tipo=_clasificar(numero, fin),
                    key_pdf=key_pdf,
                    ruta_pdf=ruta_pdf,
                    key_png=key_png,
                    ruta_png=ruta_png,
                )
            )
    return resultado


def _clasificar(numero: int, total: int) -> str:
    """Portada, contraportada o promoción, por posición en el pliego.

    La página 2 es la de lanzamiento de producto ("NUEVO PRODUCTO"), que en la práctica
    también es publicidad: va con las promociones.
    """
    if numero == 1:
        return "portada"
    if numero == total:
        return "contraportada"
    return "promocion"


def asociar_marcas_a_filas(
    logos: list[LogoMarca],
    filas_por_pagina: dict[int, list[tuple[int, float]]],
    *,
    tolerancia: float = 22.0,
) -> dict[tuple[int, int], str]:
    """Asocia cada logo a la fila cuya ancla de precio le queda más cerca.

    La tolerancia es más estrecha que la de las fotos (30 pt): los logos miden ~18 pt de
    alto y van uno por fila, así que un logo lejano es de otro producto, no del mismo.
    """
    asignacion: dict[tuple[int, int], str] = {}
    for pagina, filas in filas_por_pagina.items():
        candidatos = [logo for logo in logos if logo.pagina == pagina]
        if not candidatos:
            continue
        for orden, centro_y in filas:
            mejor = min(candidatos, key=lambda logo: abs(logo.centro_y - centro_y))
            if abs(mejor.centro_y - centro_y) <= tolerancia:
                asignacion[(pagina, orden)] = mejor.key
    return asignacion


def _volcar(datos: bytes, ext: str, destino: Path, prefijo: str) -> str:
    return _volcar_con_ruta(datos, ext, destino, prefijo)[0]


def _volcar_con_ruta(datos: bytes, ext: str, destino: Path, prefijo: str) -> tuple[str, Path]:
    sha = hashlib.sha256(datos).hexdigest()[:16]
    ruta = destino / f"{sha}.{ext}"
    if not ruta.exists():
        ruta.write_bytes(datos)
    return f"{prefijo}/{sha}.{ext}", ruta
