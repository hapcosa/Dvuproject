"""Folios de pedido.

El número que ve el cliente (`P-2026-000123`) sale de una secuencia de Postgres, no
de `max(numero)+1`: dos vendedores sincronizando a la vez desde terreno es el caso
normal, no el excepcional, y una secuencia no se equivoca bajo concurrencia.

La secuencia no se reinicia por año. El año en el folio es informativo; lo que
identifica al pedido es el correlativo, y reiniciarlo obligaría a un índice compuesto
para seguir garantizando unicidad.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

SECUENCIA = "pedido_numero_seq"


def siguiente_numero(session: Session, *, ahora: datetime | None = None) -> str:
    correlativo = session.scalar(text(f"SELECT nextval('{SECUENCIA}')"))
    anio = (ahora or datetime.now(UTC)).year
    return f"P-{anio}-{int(correlativo or 0):06d}"
