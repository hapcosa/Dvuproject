"""La maqueta del catálogo impreso, para que la web se vea como el PDF.

Son las piezas que no son texto y que el listado de productos no puede devolver: la banda
roja del encabezado —la del logo DVU— y las páginas de arte (portada, ofertas,
contraportada). Las extrae Fase 0 (`extractor/plantilla.py`) y las carga
`cargar-catalogo`; acá sólo se sirven.

El bucket no se abre al público: el contenido sale por la API, la misma política que las
fotos de producto. Ver `dvu.api.objetos` para por qué no va por URL firmada.
"""

from __future__ import annotations

import uuid as uuid_lib
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dvu.almacenamiento import (
    TAMANO_MAXIMO_BYTES,
    Almacen,
    ArchivoDemasiadoGrande,
    TipoNoPermitido,
    get_almacen,
)
from dvu.api.deps import exige_rol
from dvu.api.objetos import responder_objeto
from dvu.db.models import CatalogoActivo, CatalogoPagina, Usuario
from dvu.db.session import get_session
from dvu.extractor.pagina_subida import PaginaIlegible, convertir
from dvu.extractor.plantilla import logo_a_la_izquierda

router = APIRouter(prefix="/catalogo", tags=["catálogo"])

SessionDep = Annotated[Session, Depends(get_session)]
AlmacenDep = Annotated[Almacen, Depends(get_almacen)]
AdminDep = Annotated[Usuario, Depends(exige_rol("admin"))]

#: La banda se espeja según la página sea par o impar, como en cualquier pliego impreso.
CLAVES_BANNER = {"par": "banner_par", "impar": "banner_impar"}

#: Los mismos tres que acepta la restricción de la tabla.
TIPOS_PAGINA = frozenset({"portada", "promocion", "contraportada"})

#: Se acepta el PDF —que es lo que conserva el vector— y las imágenes que un diseñador
#: entrega de vuelta. De cualquiera de las dos se deriva la otra mitad del par.
TIPOS_PAGINA_SUBIDA = frozenset({"application/pdf", "image/jpeg", "image/png", "image/webp"})

#: Las páginas que carga el administrador van bajo su propio «archivo». El listado
#: ordena por (archivo, página), así que quedan agrupadas y después de las que salieron
#: del PDF original, dentro del bloque que les toque por tipo.
ARCHIVO_SUBIDA = "subidas-admin"


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
    activa: bool


class PaginaDisenoPatch(BaseModel):
    """Sólo lo que el administrador puede mover de una página ya cargada."""

    tipo: str | None = None
    pagina: int | None = None
    activa: bool | None = None


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
def paginas(session: SessionDep, incluir_inactivas: bool = False) -> list[CatalogoPagina]:
    """Las páginas de arte del catálogo, en el orden en que están impresas.

    `incluir_inactivas` es para la pantalla de administración: ahí hay que ver las que se
    sacaron para poder volver a ponerlas. El catálogo público llama sin el parámetro.
    """
    consulta = select(CatalogoPagina)
    if not incluir_inactivas:
        consulta = consulta.where(CatalogoPagina.activa.is_(True))
    return list(session.scalars(consulta.order_by(CatalogoPagina.archivo, CatalogoPagina.pagina)))


@router.post("/paginas", response_model=PaginaDisenoOut, status_code=status.HTTP_201_CREATED)
async def agregar_pagina(
    session: SessionDep,
    almacen: AlmacenDep,
    usuario: AdminDep,
    archivo: Annotated[UploadFile, File()],
    tipo: Annotated[str, Form()],
    pagina: Annotated[int | None, Form()] = None,
) -> CatalogoPagina:
    """Agrega una portada, una página de oferta o una contraportada.

    Se guarda el par PDF/PNG: el PDF es el que se reinserta al exportar el catálogo y el
    PNG el que ve la web. Da lo mismo cuál de los dos se suba, la otra mitad se deriva.
    """
    if tipo not in TIPOS_PAGINA:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"El tipo es uno de: {', '.join(sorted(TIPOS_PAGINA))}",
        )
    if archivo.content_type not in TIPOS_PAGINA_SUBIDA:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(
                TipoNoPermitido(
                    f"Tipo '{archivo.content_type}' no permitido para una página; se aceptan: "
                    + ", ".join(sorted(TIPOS_PAGINA_SUBIDA))
                )
            ),
        )

    datos = await archivo.read()
    if len(datos) > TAMANO_MAXIMO_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(ArchivoDemasiadoGrande(f"La página pesa {len(datos)} bytes")),
        )
    try:
        pdf, png = convertir(datos, archivo.content_type or "")
    except PaginaIlegible as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"No se pudo leer la página: {exc}"
        ) from exc

    # La key lleva un uuid y no el nombre del archivo: dos portadas distintas pueden
    # llegar con el mismo nombre desde el computador del diseñador.
    base = f"catalogo/paginas/{uuid_lib.uuid4()}"
    almacen.guardar(f"{base}.pdf", BytesIO(pdf), "application/pdf")
    almacen.guardar(f"{base}.png", BytesIO(png), "image/png")

    registro = CatalogoPagina(
        archivo=ARCHIVO_SUBIDA,
        pagina=pagina if pagina is not None else _siguiente_pagina(session),
        tipo=tipo,
        key_pdf=f"{base}.pdf",
        key_png=f"{base}.png",
    )
    session.add(registro)
    session.commit()
    session.refresh(registro)
    return registro


@router.patch("/paginas/{pagina_id}", response_model=PaginaDisenoOut)
def actualizar_pagina(
    pagina_id: int, cambios: PaginaDisenoPatch, session: SessionDep, usuario: AdminDep
) -> CatalogoPagina:
    """Cambia el tipo, la posición o si la página sale en el catálogo."""
    registro = _buscar_pagina(session, pagina_id)
    if cambios.tipo is not None:
        if cambios.tipo not in TIPOS_PAGINA:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"El tipo es uno de: {', '.join(sorted(TIPOS_PAGINA))}",
            )
        registro.tipo = cambios.tipo
    if cambios.pagina is not None:
        registro.pagina = cambios.pagina
    if cambios.activa is not None:
        registro.activa = cambios.activa
    session.commit()
    session.refresh(registro)
    return registro


@router.delete("/paginas/{pagina_id}", response_model=PaginaDisenoOut)
def quitar_pagina(pagina_id: int, session: SessionDep, usuario: AdminDep) -> CatalogoPagina:
    """Saca la página del catálogo sin borrarla.

    No se borra el registro ni el objeto del bucket: una oferta que se saca en agosto
    suele volver, y el PDF exportado el mes pasado sigue haciendo referencia a ella.
    Volver a ponerla es `PATCH` con `activa: true`.
    """
    registro = _buscar_pagina(session, pagina_id)
    registro.activa = False
    session.commit()
    session.refresh(registro)
    return registro


def _buscar_pagina(session: Session, pagina_id: int) -> CatalogoPagina:
    registro = session.get(CatalogoPagina, pagina_id)
    if registro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No existe esa página")
    return registro


def _siguiente_pagina(session: Session) -> int:
    """La posición que sigue entre las subidas a mano, para no chocar con la restricción
    de unicidad (`archivo`, `pagina`) ni obligar al administrador a llevar la cuenta."""
    ultima = session.scalar(
        select(func.max(CatalogoPagina.pagina)).where(CatalogoPagina.archivo == ARCHIVO_SUBIDA)
    )
    return (ultima or 0) + 1


@router.get("/paginas/{pagina_id}/imagen")
def imagen_de_pagina(pagina_id: int, session: SessionDep, almacen: AlmacenDep) -> Response:
    """La vista previa en PNG. Para ver la página en la web sin bajarse el PDF."""
    pagina = session.get(CatalogoPagina, pagina_id)
    if pagina is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No existe esa página")
    return responder_objeto(almacen, pagina.key_png)


@router.get("/banner/{paridad}")
def banner(paridad: str, session: SessionDep, almacen: AlmacenDep) -> Response:
    """La banda del encabezado, `par` o `impar`."""
    clave = CLAVES_BANNER.get(paridad)
    if clave is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="La banda es `par` o `impar`")
    activo = session.scalar(select(CatalogoActivo).where(CatalogoActivo.clave == clave))
    if activo is None:
        # Pasa antes de la primera extracción de plantilla. La web dibuja la suya y sigue.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Todavía no hay banda cargada")
    return responder_objeto(almacen, activo.key_objeto)
