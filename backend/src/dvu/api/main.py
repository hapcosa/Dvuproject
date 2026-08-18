"""Aplicación FastAPI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from dvu.api.routers import (
    auth,
    catalogo,
    categorias,
    clientes,
    comprobantes,
    conciliacion,
    dte,
    health,
    pagos,
    pedidos,
    productos,
    reportes,
    usuarios,
)
from dvu.config import get_settings
from dvu.web import router as web

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
        categorias.router,
        catalogo.router,
        productos.router,
        pedidos.router,
        pagos.router,
        comprobantes.router,
        conciliacion.router,
        dte.router,
        reportes.router,
        usuarios.router,
    ):
        app.include_router(router, prefix=cfg.api_prefix)

    # Prototipo web. Va montado al final para que ninguna de sus rutas tape la API.
    app.mount("/estatico", StaticFiles(directory=str(web.RAIZ / "static")), name="estatico")
    app.include_router(web.router)
    return app


app = create_app()
