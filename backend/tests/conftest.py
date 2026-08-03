"""Fixtures compartidas.

La base de los tests de integración sale de `DVU_DATABASE_URL` (es lo que hay en CI y
en `make up`). Si no hay una alcanzable, esos tests se saltan: nadie debería quedarse
sin poder correr `pytest` por no tener Postgres levantado.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from dvu.api.main import create_app
from dvu.db.base import Base
from dvu.db.session import get_session


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = os.environ.get("DVU_DATABASE_URL")
    if not url:
        pytest.skip("DVU_DATABASE_URL no definida")

    motor = create_engine(url)
    try:
        with motor.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # sin base, se salta; no es un fallo del test
        pytest.skip(f"Postgres no alcanzable: {type(exc).__name__}")

    # El esquema se crea desde los modelos, no desde Alembic: aquí se prueba el
    # comportamiento, y la migración tiene su propia verificación en CI.
    with motor.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        conn.commit()

    Base.metadata.drop_all(motor)
    Base.metadata.create_all(motor)
    yield motor
    Base.metadata.drop_all(motor)
    motor.dispose()


@pytest.fixture
def cliente_api(sesion: Session) -> Iterator[TestClient]:
    """Cliente HTTP contra la app real, compartiendo la transacción del test.

    `get_session` se sobreescribe para que el endpoint use la misma sesión: así lo
    que escribe el request es visible para el test y se revierte al terminar.
    """
    app = create_app()
    app.dependency_overrides[get_session] = lambda: sesion
    with TestClient(app) as cliente:
        yield cliente
    app.dependency_overrides.clear()


@pytest.fixture
def sesion(engine: Engine) -> Iterator[Session]:
    """Sesión aislada: cada test corre dentro de una transacción que se revierte."""
    conexion = engine.connect()
    trans = conexion.begin()
    session = sessionmaker(bind=conexion, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conexion.close()
