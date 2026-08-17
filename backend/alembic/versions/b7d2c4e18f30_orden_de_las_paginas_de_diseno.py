"""orden explícito de las páginas de diseño

Hasta acá el orden de la portada y las ofertas salía de `(archivo, pagina)`, que es de
dónde vino cada recorte, no dónde el administrador la quiere. Con dos PDF cargados eso
intercala las dos portadas con las ofertas del primero, y no hay forma de arreglarlo
desde la web: `pagina` es la página del PDF de origen y moverla mentiría sobre la
procedencia.

`orden` es la posición dentro de la sección, la que se arrastra en la pantalla de
administración. Se puebla respetando el orden que había, así que el catálogo sale igual
que antes hasta que alguien mueva algo.

Revision ID: b7d2c4e18f30
Revises: a3e71c5d90b4
Create Date: 2026-08-16 10:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2c4e18f30"
down_revision: str | None = "a3e71c5d90b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalogo_pagina",
        sa.Column("orden", sa.Integer(), nullable=False, server_default="0"),
    )
    # Numeradas dentro de su sección, que es el alcance del campo. El orden de partida es
    # el que la web ya mostraba: por archivo y por página del original.
    op.execute(
        """
        UPDATE catalogo_pagina AS p
           SET orden = n.fila
          FROM (
                SELECT id,
                       row_number() OVER (PARTITION BY tipo ORDER BY archivo, pagina) AS fila
                  FROM catalogo_pagina
               ) AS n
         WHERE p.id = n.id
        """
    )
    op.create_index("ix_catalogo_pagina_orden", "catalogo_pagina", ["tipo", "orden"])


def downgrade() -> None:
    op.drop_index("ix_catalogo_pagina_orden", table_name="catalogo_pagina")
    op.drop_column("catalogo_pagina", "orden")
