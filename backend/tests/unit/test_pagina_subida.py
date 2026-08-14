"""Convertir lo que sube el administrador al par PDF/PNG que guarda el catálogo.

Lo que se prueba es que salgan las dos mitades sea cual sea el formato de entrada: si el
`key_pdf` no fuera un PDF válido, `catalogo-pdf` saltearía la página en silencio y la
portada desaparecería del catálogo exportado sin que nadie se entere.
"""

from __future__ import annotations

import pytest

from dvu.extractor.pagina_subida import PaginaIlegible, convertir


def _pdf_de_prueba(paginas: int = 1) -> bytes:
    import fitz

    documento = fitz.open()
    for _ in range(paginas):
        documento.new_page(width=595, height=842)  # A4
    return bytes(documento.tobytes())


def _png_de_prueba(ancho: int = 40, alto: int = 60) -> bytes:
    import fitz

    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, ancho, alto))
    pix.clear_with(255)
    return bytes(pix.tobytes("png"))


def test_desde_pdf_devuelve_pdf_y_previa() -> None:
    pdf, png = convertir(_pdf_de_prueba(), "application/pdf")

    assert pdf.startswith(b"%PDF")
    assert png.startswith(b"\x89PNG")


def test_desde_imagen_arma_el_pdf_que_falta() -> None:
    """Subir un JPG tiene que dejar un `key_pdf` que el exportador pueda reinsertar."""
    pdf, png = convertir(_png_de_prueba(), "image/png")

    assert pdf.startswith(b"%PDF")
    assert png.startswith(b"\x89PNG")


def test_de_un_pdf_de_varias_paginas_se_toma_la_primera() -> None:
    """Una página de arte es una página: si el diseñador manda el pliego, se recorta."""
    import fitz

    pdf, _ = convertir(_pdf_de_prueba(paginas=3), "application/pdf")

    with fitz.open(stream=pdf, filetype="pdf") as documento:
        assert documento.page_count == 1


def test_la_previa_no_se_agranda_mas_alla_del_original() -> None:
    """Una miniatura de 40 px no se interpola a 1200: se guarda tal cual."""
    import fitz

    _, png = convertir(_png_de_prueba(ancho=40, alto=60), "image/png")

    assert fitz.Pixmap(png).width == 40


def test_una_imagen_grande_se_reduce_a_la_previa() -> None:
    """Una portada a 300 dpi no se guarda entera: la previa es para verla en pantalla."""
    import fitz

    _, png = convertir(_png_de_prueba(ancho=2400, alto=3000), "image/png")

    assert fitz.Pixmap(png).width == 1200


def test_la_previa_de_un_pdf_se_rasteriza_al_ancho_de_la_web() -> None:
    """El PDF es vector: la previa se rinde al ancho útil, no al tamaño en puntos."""
    import fitz

    _, png = convertir(_pdf_de_prueba(), "application/pdf")

    assert fitz.Pixmap(png).width == pytest.approx(1200, abs=2)


def test_un_archivo_que_no_es_pagina_falla_claro() -> None:
    with pytest.raises(PaginaIlegible):
        convertir(b"esto no es un pdf ni una imagen", "application/pdf")
