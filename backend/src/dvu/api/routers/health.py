"""Liveness y readiness.

`/health` responde sin tocar dependencias: si no responde, el proceso está muerto.
`/health/ready` sí las verifica: es lo que mira el orquestador antes de enviar tráfico.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from dvu.config import get_settings
from dvu.db.session import get_engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    cfg = get_settings()
    return {"status": "ok", "env": cfg.env, "version": "0.1.0"}


@router.get("/health/ready")
def ready(response: Response) -> dict[str, object]:
    dependencias: dict[str, str] = {}

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        dependencias["postgres"] = "ok"
    except Exception as exc:  # el detalle se reporta al operador, no se propaga
        dependencias["postgres"] = f"error: {type(exc).__name__}"

    listo = all(v == "ok" for v in dependencias.values())
    if not listo:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"ready": listo, "dependencias": dependencias}
