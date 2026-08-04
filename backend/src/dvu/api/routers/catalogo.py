"""La maqueta del catálogo impreso, para que la web se vea como el PDF.

Son las piezas que no son texto y que el listado de productos no puede devolver: la banda
roja del encabezado —la del logo DVU— y las páginas de arte (portada, ofertas,
contraportada). Las extrae Fase 0 (`extractor/plantilla.py`) y las carga
`cargar-catalogo`; acá sólo se sirven.

Todo va por redirección a URL firmada y no por URL directa: el bucket es uno solo y no se
abre al público por comodidad, la misma política que las fotos de producto.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.almacenamiento import Almacen, get_almacen
from dvu.db.models import CatalogoActivo, CatalogoPagina
from dvu.db.session import get_session
from dvu.extractor.plantilla import logo_a_la_izquierda

router = APIRouter(prefix="/catalogo", tags=["catálogo"])

SessionDep = Annotated[Session, Depends(get_session)]
AlmacenDep = Annotated[Almacen, Depends(get_almacen)]

#: La banda se espeja según la página sea par o impar, como en cualquier pliego impreso.
CLAVES_BANNER = {"par": "banner_par", "impar": "banner_impar"}


class BandaOut(BaseModel):
    """Dónde está el logo en cada versión de la banda.

    La web lo necesita para poner el folio del lado contrario, como en el impreso. Se
    mide sobre la imagen y no se guarda en la base: es una propiedad del recorte, y
    duplicarla en una columna sería otro dato que puede quedar desincronizado.
    """

    paridad: str
    logo_a_la_izquierda: bool


class PaginaDisenoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    archivo: str
    pagina: int
    #: `portada`, `promocion` o `contraportada`.
    tipo: str


@router.get("/bandas", response_model=list[BandaOut])
def bandas(session: SessionDep, almacen: AlmacenDep) -> list[BandaOut]:
    """Las bandas disponibles y de qué lado tienen el logo."""
    salida: list[BandaOut] = []
    for paridad, clave in CLAVES_BANNER.items():
        activo = session.scalar(select(CatalogoActivo).where(CatalogoActivo.clave == clave))
        if activo is None:
            continue
        datos = almacen.leer(activo.key_objeto)
        if datos is None:
            continue
        salida.append(BandaOut(paridad=paridad, logo_a_la_izquierda=logo_a_la_izquierda(datos)))
    return salida


@router.get("/paginas", response_model=list[PaginaDisenoOut])
def paginas(session: SessionDep) -> list[CatalogoPagina]:
    """Las páginas de arte del catálogo, en el orden en que están impresas."""
    return list(
        session.scalars(
            select(CatalogoPagina)
            .where(CatalogoPagina.activa.is_(True))
            .order_by(CatalogoPagina.archivo, CatalogoPagina.pagina)
        )
    )


@router.get("/paginas/{pagina_id}/imagen")
def imagen_de_pagina(pagina_id: int, session: SessionDep, almacen: AlmacenDep) -> RedirectResponse:
    """La vista previa en PNG. Para ver la página en la web sin bajarse el PDF."""
    pagina = session.get(CatalogoPagina, pagina_id)
    if pagina is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No existe esa página")
    return RedirectResponse(almacen.url_firmada(pagina.key_png), status_code=307)


@router.get("/banner/{paridad}")
def banner(paridad: str, session: SessionDep, almacen: AlmacenDep) -> RedirectResponse:
    """La banda del encabezado, `par` o `impar`."""
    clave = CLAVES_BANNER.get(paridad)
    if clave is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="La banda es `par` o `impar`")
    activo = session.scalar(select(CatalogoActivo).where(CatalogoActivo.clave == clave))
    if activo is None:
        # Pasa antes de la primera extracción de plantilla. La web dibuja la suya y sigue.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Todavía no hay banda cargada")
    return RedirectResponse(almacen.url_firmada(activo.key_objeto), status_code=307)
