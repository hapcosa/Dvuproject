"""Motor de conciliación.

Los casos son los que se dan en la operación real de DVU: el vendedor anota el nº de
operación a medias, el banco acredita al día siguiente, y dos ferreterías transfieren
la misma cifra el mismo día.
"""

from __future__ import annotations

from datetime import date

from dvu.domain.conciliacion import (
    UMBRAL_AUTOMATICO,
    Movimiento,
    PagoDeclarado,
    conciliar,
)

RUT = "76123456-0"


def _pago(**extra: object) -> PagoDeclarado:
    datos: dict[str, object] = {
        "id": 1,
        "cliente_rut": RUT,
        "monto_clp": 51122,
        "fecha_pago": date(2026, 8, 1),
        "referencia": "99887766",
    }
    datos.update(extra)
    return PagoDeclarado(**datos)  # type: ignore[arg-type]


def _movimiento(**extra: object) -> Movimiento:
    datos: dict[str, object] = {
        "id_externo": "mov-1",
        "fecha": date(2026, 8, 1),
        "monto_clp": 51122,
        "descripcion": "TRANSFERENCIA DE FERRETERIA TEST SPA",
        "referencia": "99887766",
    }
    datos.update(extra)
    return Movimiento(**datos)  # type: ignore[arg-type]


def test_numero_de_operacion_y_fecha_exacta_se_concilia_solo() -> None:
    resultado = conciliar([_movimiento()], [_pago()])

    assert len(resultado.automaticas) == 1
    assert resultado.automaticas[0].confianza >= UMBRAL_AUTOMATICO
    assert not resultado.sugerencias


def test_el_rut_en_la_glosa_tambien_identifica_a_la_ferreteria() -> None:
    """El banco no siempre trae el nº de operación; sí suele traer el RUT."""
    resultado = conciliar(
        [_movimiento(referencia=None, descripcion="TEF 76123456 FERRETERIA TEST")],
        [_pago(referencia=None)],
    )

    assert len(resultado.automaticas) == 1
    assert any("RUT" in m for m in resultado.automaticas[0].motivos)


def test_un_monto_distinto_no_es_el_mismo_pago() -> None:
    resultado = conciliar([_movimiento(monto_clp=51000)], [_pago()])

    assert not resultado.automaticas
    assert not resultado.sugerencias
    assert resultado.pagos_sin_match == (1,)
    assert resultado.movimientos_sin_match == ("mov-1",)


def test_el_banco_acredita_al_dia_siguiente_y_sigue_siendo_el_mismo_pago() -> None:
    resultado = conciliar([_movimiento(fecha=date(2026, 8, 2))], [_pago()])

    assert len(resultado.automaticas) == 1
    assert any("desfase" in m for m in resultado.automaticas[0].motivos)


def test_fuera_de_la_tolerancia_de_dias_no_se_empareja() -> None:
    resultado = conciliar([_movimiento(fecha=date(2026, 8, 20))], [_pago()])

    assert resultado.pagos_sin_match == (1,)


def test_sin_referencia_ni_rut_queda_como_sugerencia() -> None:
    """Monto y fecha solos no bastan para mover plata sin que nadie mire."""
    resultado = conciliar(
        [_movimiento(referencia=None, descripcion="ABONO")],
        [_pago(referencia=None)],
    )

    assert not resultado.automaticas
    assert len(resultado.sugerencias) == 1


def test_una_referencia_muy_corta_no_cuenta() -> None:
    """Cualquier glosa contiene un "123": pedir pocos dígitos son falsos positivos."""
    resultado = conciliar(
        [_movimiento(referencia="123", descripcion="ABONO 123")],
        [_pago(referencia="123")],
    )

    assert not resultado.automaticas
    assert len(resultado.sugerencias) == 1


def test_dos_ferreterias_con_el_mismo_monto_el_mismo_dia_no_se_resuelven_solas() -> None:
    """Caso real. Elegir una al azar es peor que preguntar."""
    resultado = conciliar(
        [_movimiento(referencia=None, descripcion="ABONO")],
        [
            _pago(id=1, referencia=None),
            _pago(id=2, cliente_rut="77987654-3", referencia=None),
        ],
    )

    assert not resultado.automaticas
    assert len(resultado.sugerencias) == 1
    assert any("empata" in m for m in resultado.sugerencias[0].motivos)
    # El pago que perdió no se descarta: queda visible para la bandeja.
    assert len(resultado.pagos_sin_match) == 1


def test_un_movimiento_no_respalda_dos_pagos() -> None:
    resultado = conciliar(
        [_movimiento()],
        [_pago(id=1), _pago(id=2, referencia="11112222")],
    )

    emparejados = [c.pago_id for c in (*resultado.automaticas, *resultado.sugerencias)]
    assert len(emparejados) == 1
    assert len(resultado.pagos_sin_match) == 1


def test_gana_el_candidato_con_mejor_evidencia() -> None:
    """Mismo monto y fecha para dos pagos, pero sólo uno trae el nº de operación."""
    resultado = conciliar(
        [_movimiento()],
        [
            _pago(id=1, referencia=None, cliente_rut="77987654-3"),
            _pago(id=2, referencia="99887766"),
        ],
    )

    assert [c.pago_id for c in resultado.automaticas] == [2]
    assert resultado.pagos_sin_match == (1,)


def test_el_resultado_es_determinista() -> None:
    movimientos = [_movimiento(), _movimiento(id_externo="mov-2", referencia="11112222")]
    pagos = [_pago(), _pago(id=2, referencia="11112222")]

    primera = conciliar(movimientos, pagos)
    segunda = conciliar(list(reversed(movimientos)), list(reversed(pagos)))

    assert sorted(c.pago_id for c in primera.automaticas) == sorted(
        c.pago_id for c in segunda.automaticas
    )


def test_nada_se_pierde_en_el_camino() -> None:
    """Todo pago entra en exactamente una de las salidas: nada se descarta en silencio."""
    pagos = [_pago(id=n, referencia=None, monto_clp=1000 * n) for n in range(1, 6)]
    movimientos = [
        _movimiento(id_externo=f"mov-{n}", monto_clp=1000 * n, referencia=None) for n in (1, 2, 9)
    ]

    resultado = conciliar(movimientos, pagos)

    clasificados = {
        *(c.pago_id for c in resultado.automaticas),
        *(c.pago_id for c in resultado.sugerencias),
        *resultado.pagos_sin_match,
    }
    assert clasificados == {p.id for p in pagos}


def test_el_resumen_dice_lo_que_paso() -> None:
    resultado = conciliar([_movimiento()], [_pago()])
    assert "1 conciliados automáticamente" in resultado.resumen()
