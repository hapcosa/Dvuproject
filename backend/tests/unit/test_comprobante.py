"""Clasificación y parseo de comprobantes de transferencia.

Los casos salen de mensajes reales del grupo «COMPROBANTES TRANSF.»: son la forma en
que el vendedor escribe de verdad, no la que sería cómoda de parsear.
"""

from __future__ import annotations

from datetime import date

import pytest

from dvu.domain.comprobante import (
    ETIQUETAS,
    ClaveDuplicado,
    DatosComprobante,
    EstadoComprobante,
    clasificar,
    clave_duplicado,
    es_abono,
    parsear_facturas,
    parsear_monto,
)

COMPLETO = DatosComprobante(
    cliente="FERRETERIA EL MARTILLO",
    facturas=("33780",),
    monto_clp=510_459,
    numero_operacion="12345678",
    fecha_transferencia=date(2026, 8, 1),
)


def test_comprobante_completo_queda_listo() -> None:
    resultado = clasificar(COMPLETO)

    assert resultado.estado is EstadoComprobante.LISTO
    assert resultado.observacion == ""
    assert resultado.faltantes == ()


@pytest.mark.parametrize(
    ("cambio", "esperado"),
    [
        ({"monto_clp": None}, EstadoComprobante.FALTA_MONTO),
        ({"cliente": ""}, EstadoComprobante.FALTA_CLIENTE),
        ({"facturas": ()}, EstadoComprobante.FALTA_FACTURA),
    ],
)
def test_un_solo_dato_ausente_da_un_estado_especifico(
    cambio: dict[str, object], esperado: EstadoComprobante
) -> None:
    """Cobranza necesita saber qué preguntar, no sólo que algo falta."""
    from dataclasses import replace

    assert clasificar(replace(COMPLETO, **cambio)).estado is esperado  # type: ignore[arg-type]


def test_varios_datos_ausentes_caen_en_falta_dato_y_se_listan() -> None:
    from dataclasses import replace

    resultado = clasificar(replace(COMPLETO, monto_clp=None, facturas=()))

    assert resultado.estado is EstadoComprobante.FALTA_DATO
    assert set(resultado.faltantes) == {"monto", "factura"}
    assert "monto" in resultado.observacion and "factura" in resultado.observacion


def test_el_abono_manda_sobre_lo_que_falte() -> None:
    """Un abono parcial es una decisión comercial, no un error de carga: se marca como
    tal aunque le falte la factura, y se arrastra qué falta."""
    from dataclasses import replace

    resultado = clasificar(replace(COMPLETO, facturas=(), detalle="abono a cuenta"))

    assert resultado.estado is EstadoComprobante.ABONO_PARCIAL
    assert resultado.faltantes == ("factura",)


def test_el_comprobante_incompleto_nunca_se_rechaza() -> None:
    """Vacío del todo sigue produciendo una clasificación: el aviso se guarda igual."""
    resultado = clasificar(DatosComprobante())

    assert resultado.estado is EstadoComprobante.FALTA_DATO
    assert resultado.etiqueta == ETIQUETAS[EstadoComprobante.FALTA_DATO]


def test_toda_etiqueta_y_color_existen_para_cada_estado() -> None:
    """Cobranza lee la etiqueta, no el valor del enum: no puede faltar ninguna."""
    for estado in EstadoComprobante:
        clasificacion = clasificar(DatosComprobante())
        assert ETIQUETAS[estado]
        assert clasificacion.color


# --- duplicados ---------------------------------------------------------------


def test_clave_de_duplicado_usa_operacion_monto_y_fecha() -> None:
    assert clave_duplicado(COMPLETO) == ClaveDuplicado(
        numero_operacion="12345678", monto_clp=510_459, fecha_transferencia=date(2026, 8, 1)
    )


@pytest.mark.parametrize("cambio", [{"numero_operacion": None}, {"monto_clp": None}])
def test_sin_numero_de_operacion_no_se_compara(cambio: dict[str, object]) -> None:
    """Dos ferreterías pueden transferir lo mismo el mismo día. Marcarlas como duplicado
    haría que cobranza desmarcara a mano todos los días y dejara de mirar el aviso."""
    from dataclasses import replace

    assert clave_duplicado(replace(COMPLETO, **cambio)) is None  # type: ignore[arg-type]


# --- parseo -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("$ 510.459", 510_459),
        ("510.459", 510_459),
        ("abono de 1 250 000 pesos", 1_250_000),
        ("transferí 68282", 68_282),
        # Bajo el mínimo creíble: es más probable que sea un dígito suelto que un monto.
        ("pagué 120", None),
        ("sin números", None),
        ("", None),
    ],
)
def test_parsear_monto(texto: str, esperado: int | None) -> None:
    assert parsear_monto(texto) == esperado


def test_el_monto_nunca_lleva_decimales() -> None:
    """El punto en Chile separa miles. Leerlo como decimal convertiría 510.459 en 510."""
    assert parsear_monto("510.459") == 510_459


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("facturas 33135 y 33134", ("33135", "33134")),
        ("factura 33780", ("33780",)),
        # El monto no es una factura, ni escrito con separador ni sin él.
        ("abono factura 33780 por 510.459", ("33780",)),
        ("abono 510459 factura 33780", ("33780",)),
        # Ni el RUT ni el número de operación.
        ("cliente 76.123.456-0 factura 33780", ("33780",)),
        ("factura 33780 op 12345678", ("33780",)),
        ("sin datos", ()),
    ],
)
def test_parsear_facturas(texto: str, esperado: tuple[str, ...]) -> None:
    assert parsear_facturas(texto) == esperado


def test_parsear_facturas_no_repite_y_conserva_el_orden() -> None:
    assert parsear_facturas("33135, 33134 y 33135") == ("33135", "33134")


@pytest.mark.parametrize(
    "texto",
    ["abono a cuenta", "va un ABONO", "pago parcial", "primera cuota", "adelanto factura 1234"],
)
def test_es_abono(texto: str) -> None:
    assert es_abono(texto)


@pytest.mark.parametrize("texto", ["pago total factura 33780", "cancela todo", ""])
def test_no_es_abono(texto: str) -> None:
    assert not es_abono(texto)
