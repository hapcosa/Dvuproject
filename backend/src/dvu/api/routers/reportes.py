"""Reportes descargables.

El Excel que hoy se llena a mano. Se genera al vuelo: no hay archivo que se
desincronice de la base.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from dvu.api.deps import SessionDep, exige_rol
from dvu.carga.excel import exportar_excel
from dvu.db.models import Usuario

router = APIRouter(prefix="/reportes", tags=["reportes"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/ventas.xlsx")
def ventas_xlsx(
    session: SessionDep,
    usuario: Annotated[Usuario, Depends(exige_rol("admin"))],
    desde: Annotated[date | None, Query(description="Inclusive")] = None,
    hasta: Annotated[date | None, Query(description="Inclusive")] = None,
) -> Response:
    """Ventas, detalle y pagos en un .xlsx de tres hojas."""
    contenido = exportar_excel(session, desde=desde, hasta=hasta)
    nombre = f"dvu-ventas-{datetime.now(UTC):%Y%m%d}.xlsx"
    return Response(
        content=contenido,
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
