"""Convierte lo que sube el administrador en el par (PDF, PNG) que guarda el catálogo.

Una página de arte se guarda dos veces: el PDF es lo que `catalogo-pdf` reinserta con
`insert_pdf` para que la portada salga con su vector y su texto intactos, y el PNG es la
vista previa que pinta la web. El extractor produce ambas desde el catálogo original
(`plantilla.py`); acá se produce lo mismo desde un archivo suelto.

Por eso se acepta tanto un PDF como una imagen: sea cual sea, se deriva la otra mitad.
Subir sólo la imagen dejaría un `key_pdf` inválido y el PDF exportado perdería la página
—`_bajar_paginas` saltea en silencio lo que no puede leer—.
"""

from __future__ import annotations

#: Ancho de la vista previa. Suficiente para verla completa en el navegador sin que una
#: portada A4 a 300 dpi se convierta en un PNG de varios MB.
_ANCHO_PREVIA = 1200


class PaginaIlegible(Exception):
    """El archivo no se pudo abrir como PDF ni como imagen."""


def convertir(datos: bytes, content_type: str) -> tuple[bytes, bytes]:
    """Devuelve `(pdf, png)` a partir de un PDF de una página o de una imagen.

    De un PDF con varias páginas se toma la primera: una página de arte es una página.
    """
    if content_type == "application/pdf":
        return _desde_pdf(datos)
    return _desde_imagen(datos)


def _desde_pdf(datos: bytes) -> tuple[bytes, bytes]:
    import fitz

    try:
        documento = fitz.open(stream=datos, filetype="pdf")
    except Exception as exc:
        raise PaginaIlegible(str(exc)) from exc

    with documento:
        if documento.page_count == 0:
            raise PaginaIlegible("El archivo no tiene ninguna página")

        pagina = documento.load_page(0)
        # El PDF es vectorial: la previa se rasteriza al ancho que necesita la web, sin
        # el tope de 1× que dejaría una portada A4 en 595 px.
        ancho = pagina.rect.width or _ANCHO_PREVIA
        escala = _ANCHO_PREVIA / ancho
        png: bytes = pagina.get_pixmap(matrix=fitz.Matrix(escala, escala)).tobytes("png")

        if documento.page_count == 1:
            return datos, png

        # Varias páginas: se recorta a la primera. El original queda intacto en `datos`.
        recorte = fitz.open()
        with recorte:
            recorte.insert_pdf(documento, from_page=0, to_page=0)
            pdf: bytes = recorte.tobytes()
    return pdf, png


def _desde_imagen(datos: bytes) -> tuple[bytes, bytes]:
    import fitz

    try:
        pixmap = fitz.Pixmap(datos)
    except Exception as exc:
        raise PaginaIlegible(str(exc)) from exc

    # Se reduce sólo si viene más grande que la previa: agrandar una miniatura no agrega
    # detalle, sólo peso.
    if pixmap.width > _ANCHO_PREVIA:
        alto = round(pixmap.height * _ANCHO_PREVIA / pixmap.width)
        pixmap = fitz.Pixmap(pixmap, _ANCHO_PREVIA, alto, None)
    png: bytes = pixmap.tobytes("png")

    try:
        documento = fitz.open(stream=datos)
    except Exception as exc:
        raise PaginaIlegible(str(exc)) from exc
    with documento:
        # `convert_to_pdf` arma una página del tamaño exacto de la imagen, sin
        # reescalarla ni recomprimirla.
        pdf: bytes = documento.convert_to_pdf()
    return pdf, png
