"""rol editor: edita el catálogo y lo imprime

El catálogo lo mantiene alguien que no tiene por qué ver cobranza ni facturación. Hasta
ahora la única forma de dejar editar era darle `admin`, o sea entregarle también la
bandeja de pagos y el SII.

Revision ID: e91c47a2b6d5
Revises: c8f1a6b34d92
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e91c47a2b6d5"
down_revision: str | None = "c8f1a6b34d92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ANTES = "rol IN ('vendedor','cliente','bodega','admin')"
DESPUES = "rol IN ('admin','editor','vendedor','cliente','bodega')"


def upgrade() -> None:
    op.drop_constraint("ck_usuario_rol", "usuario", type_="check")
    op.create_check_constraint("ck_usuario_rol", "usuario", DESPUES)


def downgrade() -> None:
    """Vuelve atrás sólo si no quedó ningún editor: el `CHECK` viejo lo rechazaría y la
    migración fallaría a mitad de camino. Se degradan a vendedor, que es el rol más
    parecido que existía antes —entra a la web y no ve cobranza—, y queda dicho acá para
    que nadie lo descubra mirando la tabla."""
    op.execute("UPDATE usuario SET rol = 'vendedor' WHERE rol = 'editor'")
    op.drop_constraint("ck_usuario_rol", "usuario", type_="check")
    op.create_check_constraint("ck_usuario_rol", "usuario", ANTES)
