"""Configuración del worker arq.

Aquí viven los trabajos que no deben bloquear una request: extracción de catálogo,
conciliación bancaria (Fase 2), emisión de DTE (Fase 2) y notificaciones.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from arq.connections import RedisSettings

from dvu.config import get_settings


async def ping(ctx: dict[str, Any]) -> str:
    """Trabajo trivial para verificar que la cola está viva."""
    return "pong"


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


class WorkerSettings:
    functions: ClassVar[list[Callable[..., Any]]] = [ping]
    redis_settings = _redis_settings()
    max_jobs = 5
    job_timeout = 60 * 30  # la extracción del catálogo completo tarda varios minutos
    keep_result = 60 * 60
