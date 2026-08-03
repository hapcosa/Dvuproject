"""Sesión de base de datos."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from dvu.config import get_settings


@lru_cache
def get_engine() -> Engine:
    cfg = get_settings()
    return create_engine(
        cfg.database_url,
        pool_pre_ping=True,  # el VPS puede cortar conexiones ociosas
        pool_size=5,
        max_overflow=10,
        echo=cfg.debug and cfg.env == "development",
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Dependencia de FastAPI. Commit al terminar bien, rollback ante cualquier error."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
