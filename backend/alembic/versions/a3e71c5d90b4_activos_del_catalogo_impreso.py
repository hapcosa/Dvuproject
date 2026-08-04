"""activos del catálogo impreso: logo de marca y páginas de diseño

En el PDF original la marca es un PNG del logo del proveedor, no texto: por eso el
extractor reportaba 1.929 filas «sin_marca». `producto.marca_logo_key` guarda ese logo.

`catalogo_pagina` guarda las páginas que son arte —portada, ofertas, contraportada—
recortadas del PDF original, para reinsertarlas verbatim al emitir el catálogo.

`catalogo_activo` es clave-valor para las piezas de maqueta (la banda roja del
encabezado, en sus dos versiones par/impar).

Revision ID: a3e71c5d90b4
Revises: 1c0fa9f42762
Create Date: 2026-08-04 14:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3e71c5d90b4"
down_revision: str | None = "1c0fa9f42762"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("producto", sa.Column("marca_logo_key", sa.String(length=255), nullable=True))

    op.create_table(
        "catalogo_pagina",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("archivo", sa.String(length=255), nullable=False),
        sa.Column("pagina", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(length=32), nullable=False),
        sa.Column("key_pdf", sa.String(length=255), nullable=False),
        sa.Column("key_png", sa.String(length=255), nullable=False),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("archivo", "pagina", name="uq_catalogo_pagina_archivo_pagina"),
        sa.CheckConstraint(
            "tipo IN ('portada','promocion','contraportada')", name="ck_catalogo_pagina_tipo"
        ),
    )

    op.create_table(
        "catalogo_activo",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("clave", sa.String(length=64), nullable=False),
        sa.Column("key_objeto", sa.String(length=255), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clave"),
    )


def downgrade() -> None:
    op.drop_table("catalogo_activo")
    op.drop_table("catalogo_pagina")
    op.drop_column("producto", "marca_logo_key")
