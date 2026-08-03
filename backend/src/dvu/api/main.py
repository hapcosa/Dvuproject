"""Aplicación FastAPI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dvu.api.routers import (
    auth,
    clientes,
    conciliacion,
    dte,
    health,
    pagos,
    pedidos,
    productos,
    reportes,
)
from dvu.config import get_settings

log = logging.getLogger("dvu")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = get_settings()
    # Falla al arrancar, no en la primera request, si la config de producción es insegura.
    cfg.validar_para_produccion()
    logging.basicConfig(
        level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    log.info("DVU API iniciando (env=%s)", cfg.env)
    yield
    log.info("DVU API detenida")


def create_app() -> FastAPI:
    cfg = get_settings()

    app = FastAPI(
        title="DVU API",
        description="Ecommerce B2B, app de vendedores y conciliación de pagos — Comercial DVU SpA",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not cfg.es_produccion else None,
        redoc_url=None,
    )

    if cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health.router)
    for router in (
        auth.router,
        clientes.router,
        productos.router,
        pedidos.router,
        pagos.router,
        conciliacion.router,
        dte.router,
        reportes.router,
    ):
        app.include_router(router, prefix=cfg.api_prefix)
    return app


app = create_app()
