"""Validación de lo que se sube al almacén.

Comprobante y foto de catálogo no aceptan lo mismo a propósito: el comprobante es
evidencia que hay que conservar tal como la mandó el vendedor (el banco entrega PDF y el
iPhone manda HEIC), y la foto de producto se pinta en un `<img>` del catálogo.
"""

from __future__ import annotations

import pytest

from dvu.almacenamiento import (
    TAMANO_MAXIMO_BYTES,
    ArchivoDemasiadoGrande,
    TipoNoPermitido,
    key_imagen_producto,
    validar_comprobante,
    validar_imagen_producto,
)


@pytest.mark.parametrize(
    ("tipo", "extension"),
    [("image/jpeg", "jpg"), ("image/png", "png"), ("image/webp", "webp")],
)
def test_formatos_que_el_navegador_sabe_pintar(tipo: str, extension: str) -> None:
    assert validar_imagen_producto(tipo, 1024) == extension


@pytest.mark.parametrize("tipo", ["application/pdf", "image/heic", "text/html", None])
def test_lo_que_no_se_puede_pintar_se_rechaza(tipo: str | None) -> None:
    """HEIC lo manda el iPhone tal cual y ningún navegador lo muestra: quedaría un
    hueco en la grilla del catálogo. Como comprobante sí se acepta."""
    with pytest.raises(TipoNoPermitido):
        validar_imagen_producto(tipo, 1024)


def test_el_comprobante_sigue_aceptando_pdf_y_heic() -> None:
    assert validar_comprobante("application/pdf", 1024) == "pdf"
    assert validar_comprobante("image/heic", 1024) == "heic"


def test_la_imagen_demasiado_grande_se_rechaza() -> None:
    with pytest.raises(ArchivoDemasiadoGrande):
        validar_imagen_producto("image/jpeg", TAMANO_MAXIMO_BYTES + 1)


def test_el_tamano_desconocido_no_bloquea() -> None:
    """El navegador no siempre manda `content-length` en el multipart."""
    assert validar_imagen_producto("image/jpeg", None) == "jpg"


def test_la_key_va_por_sku_para_que_resubir_pise_la_anterior() -> None:
    assert key_imagen_producto("DVU-PR49573", "jpg") == "catalogo/DVU-PR49573.jpg"
