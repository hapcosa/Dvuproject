"""sin rol cliente: el sistema es interno

`cliente` estaba pensado para que el ferretero armara su propio pedido, o sea para un
ecommerce. Eso sería otro servidor y otro stack, y todavía es una idea. Mientras tanto un
rol así es una cuenta externa dentro del sistema de la casa.

Revision ID: f3a8b21c704e
Revises: e91c47a2b6d5
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f3a8b21c704e"
down_revision: str | None = "e91c47a2b6d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ANTES = "rol IN ('admin','editor','vendedor','cliente','bodega')"
DESPUES = "rol IN ('admin','editor','vendedor','bodega')"


def upgrade() -> None:
    """Las cuentas `cliente` que haya quedan **desactivadas**, no reasignadas a un rol de
    trabajo.

    Pasarlas a `vendedor` sería el atajo obvio y es justo lo que no se puede hacer: le
    entregaría a una cuenta de fuera la cartera completa de ferreterías y todos los
    pedidos. Desactivada no entra a nada, y decidir qué hacer con ella es de una persona
    que sepa de quién era. Se les pone `bodega` sólo porque la columna necesita un valor
    que el `CHECK` acepte; reactivar una sin mirar quién es sería el mismo error en
    diferido.
    """
    op.execute("UPDATE usuario SET activo = false, rol = 'bodega' WHERE rol = 'cliente'")
    op.drop_constraint("ck_usuario_rol", "usuario", type_="check")
    op.create_check_constraint("ck_usuario_rol", "usuario", DESPUES)


def downgrade() -> None:
    """Devuelve el rol a la lista, pero no las cuentas: cuáles eran `cliente` se perdió
    en el `upgrade` a propósito, porque volver a activarlas automáticamente es lo que se
    está evitando."""
    op.drop_constraint("ck_usuario_rol", "usuario", type_="check")
    op.create_check_constraint("ck_usuario_rol", "usuario", ANTES)
