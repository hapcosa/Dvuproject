"""el folio del pedido se asigna al enviarlo, no al crearlo

Las listas que el vendedor arma en terreno ahora viven en el servidor como pedidos en
estado `borrador`, para que sobrevivan a cerrar la pestaña y se puedan retomar desde
otro equipo. Un borrador **no es un documento comercial**: darle folio al crearlo
quemaría un número de `pedido_numero_seq` por cada lista que nunca se envía, y eso se
ve después como huecos en la correlatividad que alguien tiene que explicar.

Por eso `numero` pasa a ser nulo mientras el pedido es borrador. Sigue siendo único: en
Postgres los nulos no chocan entre sí en un índice único.

Revision ID: c8f1a6b34d92
Revises: b7d2c4e18f30
Create Date: 2026-08-16 17:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8f1a6b34d92"
down_revision: str | None = "b7d2c4e18f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("pedido", "numero", existing_type=sa.String(24), nullable=True)


def downgrade() -> None:
    # Los borradores no tienen folio, así que hay que darles uno antes de volver a
    # exigirlo. Sale de la misma secuencia que el resto: son pedidos incompletos, no
    # documentos, pero un folio inventado a mano rompería la unicidad.
    op.execute(
        """
        UPDATE pedido
           SET numero = 'P-' || to_char(coalesce(creado_en, now()), 'YYYY')
                        || '-' || lpad(nextval('pedido_numero_seq')::text, 6, '0')
         WHERE numero IS NULL
        """
    )
    op.alter_column("pedido", "numero", existing_type=sa.String(24), nullable=False)
