"""Cartola bancaria vía agregador.

DVU no tiene API del banco: usa un agregador (Fintoc o Floid) que normaliza la cartola
de la cuenta del dueño. Este módulo la trae; el matching contra los pagos declarados
está en `dvu.domain.conciliacion`, que es lógica pura.

En desarrollo el proveedor es `fake` y lee un archivo. Eso permite ensayar la
conciliación con una cartola de prueba —o con la cartola real exportada a mano— sin
credenciales y sin tocar la cuenta del dueño.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from dvu.config import get_settings
from dvu.domain.conciliacion import Movimiento


class ErrorBanco(Exception):
    """Falla al traer la cartola. Nunca se traduce a "no hay movimientos": una cartola
    vacía por error haría que todo pago pareciera sin respaldo."""


class Banco(Protocol):
    nombre: str

    def movimientos(self, desde: date, hasta: date) -> list[Movimiento]: ...


class BancoFake:
    """Lee la cartola de un JSONL en disco.

    Formato, una línea por movimiento:

        {"id": "mov-1", "fecha": "2026-08-01", "monto_clp": 51122,
         "descripcion": "TEF 76123456 FERRETERIA TEST", "referencia": "99887766"}

    Si el archivo no existe devuelve una lista vacía: el stack levanta igual.
    """

    nombre = "fake"

    def __init__(self, ruta: Path | None = None) -> None:
        self._ruta = ruta or get_settings().cartola_fake_path

    def movimientos(self, desde: date, hasta: date) -> list[Movimiento]:
        if not self._ruta.exists():
            return []

        salida: list[Movimiento] = []
        for numero, linea in enumerate(self._ruta.read_text(encoding="utf-8").splitlines(), 1):
            if not linea.strip():
                continue
            try:
                salida.append(_desde_json(json.loads(linea)))
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                raise ErrorBanco(f"{self._ruta}:{numero} no es un movimiento válido") from exc

        return [m for m in salida if desde <= m.fecha <= hasta]


class BancoFintoc:
    """Fintoc: agregador chileno con API de movimientos por cuenta.

    Sin `DVU_BANCO_API_KEY` no se instancia: es preferible fallar al arrancar a
    sincronizar contra nada y dejar la bandeja en blanco.
    """

    nombre = "fintoc"
    BASE = "https://api.fintoc.com/v1"

    def __init__(self) -> None:
        cfg = get_settings()
        if not cfg.banco_api_key or not cfg.banco_cuenta_id:
            raise ErrorBanco("Faltan DVU_BANCO_API_KEY y DVU_BANCO_CUENTA_ID")
        self._api_key = cfg.banco_api_key
        self._cuenta = cfg.banco_cuenta_id
        self._link_token = cfg.banco_link_token

    def movimientos(self, desde: date, hasta: date) -> list[Movimiento]:
        import httpx

        try:
            respuesta = httpx.get(
                f"{self.BASE}/accounts/{self._cuenta}/movements",
                headers={"Authorization": self._api_key},
                params={
                    "link_token": self._link_token,
                    "since": desde.isoformat(),
                    "until": hasta.isoformat(),
                    "per_page": 300,
                },
                timeout=30,
            )
            respuesta.raise_for_status()
        except httpx.HTTPError as exc:
            raise ErrorBanco(f"No se pudo leer la cartola: {exc}") from exc

        return [_desde_fintoc(item) for item in respuesta.json()]


def get_banco() -> Banco:
    cfg = get_settings()
    if cfg.banco_proveedor == "fintoc":
        return BancoFintoc()
    return BancoFake()


# --- normalización -----------------------------------------------------------


def _desde_json(item: dict[str, Any]) -> Movimiento:
    return Movimiento(
        id_externo=str(item["id"]),
        fecha=date.fromisoformat(item["fecha"]),
        monto_clp=int(item["monto_clp"]),
        descripcion=str(item.get("descripcion") or ""),
        referencia=_opcional(item.get("referencia")),
        rut_contraparte=_opcional(item.get("rut_contraparte")),
    )


def _desde_fintoc(item: dict[str, Any]) -> Movimiento:
    """Fintoc entrega el monto en la moneda de la cuenta; en CLP no hay decimales.

    Los cargos vienen negativos. Se conservan tal cual: un egreso mal interpretado como
    abono inventaría un pago que nadie hizo.
    """
    contraparte = item.get("counterparty") or {}
    return Movimiento(
        id_externo=str(item["id"]),
        fecha=date.fromisoformat(str(item["post_date"])[:10]),
        monto_clp=int(item["amount"]),
        descripcion=str(item.get("description") or ""),
        referencia=_opcional(item.get("reference_id")),
        rut_contraparte=_opcional(contraparte.get("holder_id")),
    )


def _opcional(valor: object) -> str | None:
    texto = str(valor).strip() if valor is not None else ""
    return texto or None
