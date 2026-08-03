"""Lectura de la cartola.

Lo importante no es parsear JSON: es que un error de lectura **no** se confunda con
"no hubo movimientos". Una cartola vacía por falla haría que todo pago pareciera sin
respaldo, y eso manda a revisión manual el trabajo de un día entero.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from dvu.integraciones.banco import BancoFake, ErrorBanco, _desde_fintoc

LINEA = {
    "id": "mov-1",
    "fecha": "2026-08-01",
    "monto_clp": 51122,
    "descripcion": "TEF DE 76123456-0 FERRETERIA TEST",
    "referencia": "99887766",
    "rut_contraparte": "76123456-0",
}


def _cartola(tmp_path: Path, *lineas: dict[str, object]) -> Path:
    ruta = tmp_path / "cartola.jsonl"
    ruta.write_text("\n".join(json.dumps(linea) for linea in lineas), encoding="utf-8")
    return ruta


def test_lee_un_movimiento(tmp_path: Path) -> None:
    banco = BancoFake(_cartola(tmp_path, LINEA))

    movimientos = banco.movimientos(date(2026, 7, 1), date(2026, 8, 31))

    assert len(movimientos) == 1
    assert movimientos[0].monto_clp == 51122
    assert movimientos[0].rut_contraparte == "76123456-0"


def test_sin_archivo_no_falla(tmp_path: Path) -> None:
    """El stack tiene que levantar aunque nadie haya generado la cartola de prueba."""
    assert (
        BancoFake(tmp_path / "no-existe.jsonl").movimientos(date(2026, 1, 1), date(2026, 12, 31))
        == []
    )


def test_una_linea_corrupta_es_un_error_no_una_cartola_vacia(tmp_path: Path) -> None:
    ruta = tmp_path / "cartola.jsonl"
    ruta.write_text(json.dumps(LINEA) + "\n{esto no es json}\n", encoding="utf-8")

    with pytest.raises(ErrorBanco, match=":2"):
        BancoFake(ruta).movimientos(date(2026, 1, 1), date(2026, 12, 31))


def test_filtra_por_rango(tmp_path: Path) -> None:
    vieja = LINEA | {"id": "mov-vieja", "fecha": "2026-06-01"}
    banco = BancoFake(_cartola(tmp_path, LINEA, vieja))

    movimientos = banco.movimientos(date(2026, 7, 1), date(2026, 8, 31))

    assert [m.id_externo for m in movimientos] == ["mov-1"]


def test_las_lineas_en_blanco_se_ignoran(tmp_path: Path) -> None:
    ruta = tmp_path / "cartola.jsonl"
    ruta.write_text(f"\n{json.dumps(LINEA)}\n\n", encoding="utf-8")

    assert len(BancoFake(ruta).movimientos(date(2026, 1, 1), date(2026, 12, 31))) == 1


def test_un_cargo_conserva_su_signo() -> None:
    """Interpretar un egreso como abono inventaría un pago que nadie hizo."""
    movimiento = _desde_fintoc(
        {
            "id": "ft-1",
            "post_date": "2026-08-01T14:23:00Z",
            "amount": -35000,
            "description": "PAGO PROVEEDOR",
        }
    )

    assert movimiento.monto_clp == -35000
    assert movimiento.fecha == date(2026, 8, 1)


def test_fintoc_sin_contraparte_no_inventa_rut() -> None:
    movimiento = _desde_fintoc(
        {"id": "ft-2", "post_date": "2026-08-01", "amount": 51122, "counterparty": None}
    )

    assert movimiento.rut_contraparte is None
    assert movimiento.referencia is None


def test_fintoc_normaliza_la_contraparte() -> None:
    movimiento = _desde_fintoc(
        {
            "id": "ft-3",
            "post_date": "2026-08-01",
            "amount": 51122,
            "reference_id": " 99887766 ",
            "counterparty": {"holder_id": "76123456-0"},
        }
    )

    assert movimiento.referencia == "99887766"
    assert movimiento.rut_contraparte == "76123456-0"
