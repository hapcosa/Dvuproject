"""tabla marca con logo

En el catálogo impreso la marca es el logo del proveedor, no su nombre escrito: el
extractor recortó 220 imágenes distintas para 1275 productos, pero no puede leerlas.
Esta tabla es donde alguien les pone nombre, y `producto.marca_id` el resultado.

Va aparte de `producto.marca_impresa` y `producto.marca_logo_key` a propósito: esas dos
las reescribe `make cargar-catalogo` sin condición en cada recarga, así que curar marcas
ahí se perdería en la próxima pasada del PDF.

Revision ID: f573656d91c4
Revises: f3a8b21c704e
Create Date: 2026-08-18 13:31:53.545379
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f573656d91c4"
down_revision: str | None = "f3a8b21c704e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_MARCA = "fk_producto_marca_id"


def upgrade() -> None:
    op.create_table(
        "marca",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("nombre", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("logo_key", sa.String(length=255), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_marca_slug"),
    )

    # `alter_column ... new_column_name` y no drop+add: la columna trae 128 valores del
    # extractor y borrarla los perdería. Son medidas mal clasificadas —'1/2"', 'X'— pero
    # son su salida literal, y el nombre nuevo existe justamente para dejar de
    # confundirlas con una marca de verdad.
    op.alter_column("producto", "marca", new_column_name="marca_impresa")

    op.add_column("producto", sa.Column("marca_id", sa.BigInteger(), nullable=True))
    op.create_index(op.f("ix_producto_marca_id"), "producto", ["marca_id"], unique=False)
    op.create_foreign_key(FK_MARCA, "producto", "marca", ["marca_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint(FK_MARCA, "producto", type_="foreignkey")
    op.drop_index(op.f("ix_producto_marca_id"), table_name="producto")
    op.drop_column("producto", "marca_id")
    op.alter_column("producto", "marca_impresa", new_column_name="marca")
    op.drop_table("marca")
