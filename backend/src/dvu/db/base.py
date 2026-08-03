"""Base declarativa y mixins compartidos."""

from __future__ import annotations

import uuid as uuid_lib
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from sqlalchemy import BigInteger, DateTime, Numeric, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: CLP no tiene decimales: 12 enteros, 0 decimales. Nunca float para dinero.
DineroCLP = Annotated[Decimal, mapped_column(Numeric(12, 0))]

pk = Annotated[int, mapped_column(BigInteger, primary_key=True)]


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UUIDMixin:
    """Identificador público. Los IDs secuenciales no salen de la BD."""

    uuid: Mapped[uuid_lib.UUID] = mapped_column(
        PgUUID(as_uuid=True), default=uuid_lib.uuid4, unique=True, index=True, nullable=False
    )
