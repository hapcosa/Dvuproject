"""Reportes descargables.

El Excel que hoy se llena a mano. Se genera al vuelo: no hay archivo que se
desincronice de la base.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from dvu.almacenamiento import Almacen, get_almacen
from dvu.api.deps import SessionDep, UsuarioDep, exige_rol
from dvu.carga.catalogo_impreso import exportar_catalogo_pdf
from dvu.carga.excel import exportar_excel
from dvu.db.models import Usuario

router = APIRouter(prefix="/reportes", tags=["reportes"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#: Techo del PDF. Con fotos, el catálogo completo son ~140 páginas y ~13 s de CPU; más
#: que eso es un pedido que se hizo mal, no un catálogo que alguien vaya a leer.
MAX_PRODUCTOS_PDF = 3000


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


@router.get("/catalogo.pdf")
def catalogo_pdf(
    session: SessionDep,
    usuario: UsuarioDep,
    almacen: Annotated[Almacen, Depends(get_almacen)],
    categoria: Annotated[str | None, Query(description="Slug; vacío = catálogo completo")] = None,
    q: Annotated[str | None, Query(description="Filtra por descripción")] = None,
    con_imagenes: Annotated[bool, Query(description="False da la lista de precios")] = True,
) -> Response:
    """El catálogo impreso, con el diseño del PDF original y los precios de hoy.

    Pide sesión aunque el catálogo web sea público: generarlo entero cuesta ~13 s de CPU
    y varios cientos de lecturas al almacén, y eso abierto al mundo es una palanca de
    denegación de servicio gratis. Cualquier rol sirve — el vendedor lo manda por
    WhatsApp, que es exactamente para lo que se pide.
    """
    contenido = exportar_catalogo_pdf(
        session,
        almacen,
        categoria=categoria,
        q=q,
        con_imagenes=con_imagenes,
        limite=MAX_PRODUCTOS_PDF,
    )
    sufijo = f"-{categoria}" if categoria else ""
    nombre = f"catalogo-dvu{sufijo}-{datetime.now(UTC):%Y%m%d}.pdf"
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
