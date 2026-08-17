"""Fixtures compartidas.

El servidor de la base sale de `DVU_DATABASE_URL` (es lo que hay en CI y en `make up`),
pero **la base en sí nunca es la de esa URL**: se le pega el sufijo `_test` y se crea si
no existe. Ver `_url_de_test`. Si no hay Postgres alcanzable, los tests de integración se
saltan: nadie debería quedarse sin poder correr `pytest` por no tenerlo levantado.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from dvu.api.main import create_app
from dvu.db.base import Base
from dvu.db.session import get_session

#: Sufijo obligatorio de la base de tests.
SUFIJO_TEST = "_test"


def _url_de_test(url: str) -> str:
    """Manda la URL a `<base>_test`, salvo que ya apunte a una.

    Esto no es cosmético. La fixture hace `drop_all` al empezar **y al terminar**, así
    que apuntar los tests a la base del stack se lleva puesto el catálogo cargado sin
    preguntar y sin aviso: el drop es lo último que corre pytest, después de que ya
    imprimió que todo pasó. Pasó una vez; el sufijo lo hace imposible.
    """
    partes = urlsplit(url)
    nombre = partes.path.lstrip("/")
    if nombre.endswith(SUFIJO_TEST):
        return url
    return urlunsplit(partes._replace(path=f"/{nombre}{SUFIJO_TEST}"))


def _crear_base(url: str) -> None:
    """Crea la base de tests si falta, conectándose a `postgres` en el mismo servidor.

    `CREATE DATABASE` no corre dentro de una transacción, de ahí el AUTOCOMMIT.
    """
    partes = urlsplit(url)
    nombre = partes.path.lstrip("/")
    admin = create_engine(
        urlunsplit(partes._replace(path="/postgres")), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin.connect() as conn:
            existe = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :nombre"), {"nombre": nombre}
            ).scalar()
            if not existe:
                # El nombre lo arma `_url_de_test` a partir de la config, no del test:
                # no hay entrada de usuario que interpolar acá.
                conn.execute(text(f'CREATE DATABASE "{nombre}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    configurada = os.environ.get("DVU_DATABASE_URL")
    if not configurada:
        pytest.skip("DVU_DATABASE_URL no definida")
    url = _url_de_test(configurada)

    try:
        _crear_base(url)
        motor = create_engine(url)
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
