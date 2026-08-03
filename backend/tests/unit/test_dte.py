"""Reglas de emisión de documentos tributarios. Lógica pura, sin base ni SII."""

from __future__ import annotations

from typing import Any

import pytest

from dvu.domain.dte import (
    DocumentoDte,
    Emisor,
    LineaDte,
    NoFacturable,
    Receptor,
    TipoDte,
    construir,
    validar_emision,
)

EMISOR = Emisor(rut="76000000-0", razon_social="COMERCIAL DVU SPA", giro="DISTRIBUCION")
RECEPTOR = Receptor(rut="76123456-0", razon_social="FERRETERIA TEST SPA")
LINEAS = [
    LineaDte(
        sku="DVU-PR49573",
        descripcion="LIQUIDO DE FRENO FEDERAL",
        cantidad=24,
        precio_unitario_clp=1790,
        total_clp=42960,
    )
]


def _construir(tipo: TipoDte = TipoDte.FACTURA_AFECTA, **extra: Any) -> DocumentoDte:
    kwargs: dict[str, Any] = {
        "emisor": EMISOR,
        "receptor": RECEPTOR,
        "lineas": LINEAS,
        "neto_clp": 42960,
        "iva_clp": 8162,
        "total_clp": 51122,
    }
    kwargs.update(extra)
    return construir(tipo, **kwargs)


@pytest.mark.parametrize("estado", ["confirmado", "preparacion", "despachado", "entregado"])
def test_se_factura_desde_confirmado_en_adelante(estado: str) -> None:
    validar_emision(TipoDte.FACTURA_AFECTA, estado)


@pytest.mark.parametrize("estado", ["borrador", "enviado", "anulado"])
def test_no_se_factura_antes_de_confirmar(estado: str) -> None:
    """Un pedido que todavía puede cambiar no se factura: corregir cuesta una nota de
    crédito."""
    with pytest.raises(NoFacturable):
        validar_emision(TipoDte.FACTURA_AFECTA, estado)


def test_la_guia_acompana_la_mercaderia() -> None:
    validar_emision(TipoDte.GUIA_DESPACHO, "preparacion")

    with pytest.raises(NoFacturable, match="guía"):
        validar_emision(TipoDte.GUIA_DESPACHO, "entregado")


def test_sin_factura_no_hay_nota_de_credito() -> None:
    with pytest.raises(NoFacturable, match="No hay factura"):
        validar_emision(TipoDte.NOTA_CREDITO, "confirmado", ya_emitido=False)

    validar_emision(TipoDte.NOTA_CREDITO, "confirmado", ya_emitido=True)


def test_un_documento_sin_lineas_no_existe() -> None:
    with pytest.raises(NoFacturable, match="sin líneas"):
        _construir(lineas=[])


def test_la_nota_de_credito_exige_folio_y_motivo() -> None:
    with pytest.raises(NoFacturable, match="folio"):
        _construir(TipoDte.NOTA_CREDITO, motivo="Precio mal aplicado")

    with pytest.raises(NoFacturable, match="motivo"):
        _construir(TipoDte.NOTA_CREDITO, referencia_folio=17)


def test_la_nota_de_credito_referencia_la_factura() -> None:
    nota = _construir(TipoDte.NOTA_CREDITO, referencia_folio=17, motivo="Precio mal aplicado")

    assert nota.referencia_folio == 17
    assert nota.referencia_tipo is TipoDte.FACTURA_AFECTA


def test_los_totales_no_se_recalculan() -> None:
    """La factura tiene que decir exactamente lo que se cobró, aunque el IVA no cuadre
    al peso: el pedido ya congeló sus montos."""
    documento = _construir(neto_clp=42960, iva_clp=8161, total_clp=51121)

    assert (documento.neto_clp, documento.iva_clp, documento.total_clp) == (
        42960,
        8161,
        51121,
    )


def test_los_tipos_son_los_del_sii() -> None:
    assert (TipoDte.FACTURA_AFECTA, TipoDte.GUIA_DESPACHO, TipoDte.NOTA_CREDITO) == (33, 52, 61)
