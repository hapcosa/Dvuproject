"""secuencia de folios de pedido

Revision ID: f842a1b09129
Revises: d49e04f8dc27
Create Date: 2026-08-03 13:54:56.645845
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'f842a1b09129'
down_revision: str | None = 'd49e04f8dc27'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Los folios se generan con nextval, no con max(numero)+1: varios vendedores
    # sincronizan desde terreno al mismo tiempo.
    op.execute("CREATE SEQUENCE IF NOT EXISTS pedido_numero_seq START WITH 1")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS pedido_numero_seq")
