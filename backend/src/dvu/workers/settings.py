"""Configuración del worker arq.

Aquí viven los trabajos que no deben bloquear una request: extracción de catálogo,
conciliación bancaria (Fase 2), emisión de DTE (Fase 2) y notificaciones.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from dvu.config import get_settings

log = logging.getLogger("dvu.worker")


async def ping(ctx: dict[str, Any]) -> str:
    """Trabajo trivial para verificar que la cola está viva."""
    return "pong"


async def conciliar_cartola(ctx: dict[str, Any]) -> str:
    """Sincroniza la cartola y concilia lo que supere el umbral.

    Corre solo, pero **no cierra nada solo**: lo que no cuadra queda en la bandeja de
    excepciones. Un fallo del agregador se registra y se reintenta al ciclo siguiente;
    nunca se traduce a "no hubo movimientos", que dejaría todo pago sin respaldo.
    """
    from dvu.conciliacion import sincronizar_y_conciliar
    from dvu.db.session import sesion
    from dvu.integraciones.banco import ErrorBanco

    try:
        with sesion() as session:
            resumen = sincronizar_y_conciliar(session)
    except ErrorBanco as exc:
        log.error("No se pudo conciliar: %s", exc)
        return f"error: {exc}"

    log.info("Conciliación: %s", resumen.resumen().replace("\n", " | "))
    return resumen.resumen()


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


class WorkerSettings:
    functions: ClassVar[list[Callable[..., Any]]] = [ping, conciliar_cartola]
    #: Cada media hora en horario hábil chileno. El banco no publica los movimientos al
    #: instante; consultar más seguido sólo gasta cuota del agregador.
    cron_jobs: ClassVar[list[Any]] = [
        cron(conciliar_cartola, hour=set(range(9, 19)), minute={0, 30})
    ]
    redis_settings = _redis_settings()
    max_jobs = 5
    job_timeout = 60 * 30  # la extracción del catálogo completo tarda varios minutos
    keep_result = 60 * 60
