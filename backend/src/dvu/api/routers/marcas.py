"""Marcas del catálogo.

En el impreso la marca es el logo del proveedor, no su nombre escrito. El extractor
recorta bien esos logos —1275 productos los tienen, en 220 imágenes distintas— pero no
puede leerlos: quedan como imágenes anónimas que no se pueden listar ni filtrar. Estos
endpoints son para ponerles nombre una vez y que se lleven sus productos de una.

Se lee sin autenticar, igual que el catálogo: es la vitrina. Editar es de `editor`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dvu.almacenamiento import (
    Almacen,
    ArchivoDemasiadoGrande,
    TipoNoPermitido,
    get_almacen,
    key_logo_marca,
    validar_imagen_producto,
)
from dvu.api.deps import exige_rol
from dvu.api.objetos import responder_objeto
from dvu.db.models import Marca, Producto, Usuario
from dvu.db.session import get_session
from dvu.domain.marcas import slug_de
from dvu.domain.roles import EDITOR

router = APIRouter(prefix="/marcas", tags=["catálogo"])

SessionDep = Annotated[Session, Depends(get_session)]
EditorDep = Annotated[Usuario, Depends(exige_rol(EDITOR))]
AlmacenDep = Annotated[Almacen, Depends(get_almacen)]


class MarcaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    nombre: str
    activo: bool
    #: Si tiene logo, está en `GET /marcas/{slug}/logo`. Sin logo la marca sirve igual:
    #: se muestra el nombre escrito, que es más de lo que hay hoy.
    tiene_logo: bool = False
    productos: int = 0


class MarcaEntrada(BaseModel):
    nombre: str = Field(min_length=1, max_length=128)


class MarcaParche(BaseModel):
    """Renombrar y activar. El slug no se toca: es lo que apunta desde los enlaces."""

    nombre: str | None = Field(default=None, min_length=1, max_length=128)
    activo: bool | None = None


class LogoHuerfano(BaseModel):
    """Un recorte del extractor que todavía no es de ninguna marca."""

    logo_key: str
    #: Cuántos productos sin marca lo llevan. Ordena la lista: nombrar los diez
    #: primeros cubre más de la mitad del catálogo.
    productos: int
    #: Un SKU cualquiera de los que lo usan, para poder mirar la imagen antes de
    #: nombrarla: se ve en `GET /productos/{sku}/marca`.
    sku_muestra: str


class Adopcion(BaseModel):
    logo_key: str = Field(min_length=1, max_length=255)


class ResultadoAdopcion(BaseModel):
    marca: MarcaOut
    productos_asignados: int


def _a_salida(marca: Marca, productos: int = 0) -> MarcaOut:
    return MarcaOut(
        slug=marca.slug,
        nombre=marca.nombre,
        activo=marca.activo,
        tiene_logo=marca.logo_key is not None,
        productos=productos,
    )


def _buscar(session: Session, slug: str) -> Marca:
    marca = session.scalar(select(Marca).where(Marca.slug == slug))
    if marca is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe la marca {slug}")
    return marca


def _conteos(session: Session) -> dict[int, int]:
    """Cuántos productos activos tiene cada marca."""
    filas = (
        session.execute(
            select(Producto.marca_id, func.count())
            .where(Producto.activo.is_(True), Producto.marca_id.is_not(None))
            .group_by(Producto.marca_id)
        )
        .tuples()
        .all()
    )
    # El `is_not(None)` de arriba ya los descarta, pero la columna es anulable y el
    # tipo lo sigue diciendo.
    return {marca_id: cuantos for marca_id, cuantos in filas if marca_id is not None}


@router.get("", response_model=list[MarcaOut])
def listar(session: SessionDep) -> list[MarcaOut]:
    conteos = _conteos(session)
    filas = session.scalars(select(Marca).order_by(Marca.nombre)).all()
    return [_a_salida(m, conteos.get(m.id, 0)) for m in filas]


@router.get("/logos-sin-marca", response_model=list[LogoHuerfano])
def logos_sin_marca(session: SessionDep, usuario: EditorDep) -> list[LogoHuerfano]:
    """Los recortes del extractor que todavía no tienen nombre, el más usado primero.

    Es la lista de trabajo del editor: cada fila es una imagen y cuántos productos
    quedan colgando de ella. Se responde ordenada por cantidad porque el reparto es
    muy desparejo —el logo más repetido cubre 133 productos y la cola son marcas de
    un producto— y conviene empezar por donde rinde.
    """
    filas = session.execute(
        select(
            Producto.marca_logo_key,
            func.count(),
            func.min(Producto.sku),
        )
        .where(
            Producto.activo.is_(True),
            Producto.marca_logo_key.is_not(None),
            Producto.marca_id.is_(None),
        )
        .group_by(Producto.marca_logo_key)
        .order_by(func.count().desc(), Producto.marca_logo_key)
    ).all()
    return [
        LogoHuerfano(logo_key=key, productos=cuantos, sku_muestra=sku)
        for key, cuantos, sku in filas
        if key is not None
    ]


@router.post("", response_model=MarcaOut, status_code=status.HTTP_201_CREATED)
def crear(entrada: MarcaEntrada, session: SessionDep, usuario: EditorDep) -> MarcaOut:
    slug = slug_de(entrada.nombre)
    if not slug:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"'{entrada.nombre}' no deja nada utilizable como nombre de marca",
        )
    marca = Marca(nombre=entrada.nombre.strip(), slug=slug, activo=True)
    session.add(marca)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Ya existe una marca con el slug '{slug}'"
        ) from exc
    return _a_salida(marca)


@router.patch("/{slug}", response_model=MarcaOut)
def actualizar(slug: str, parche: MarcaParche, session: SessionDep, usuario: EditorDep) -> MarcaOut:
    marca = _buscar(session, slug)
    if parche.nombre is not None:
        marca.nombre = parche.nombre.strip()
    if parche.activo is not None:
        marca.activo = parche.activo
    session.flush()
    return _a_salida(marca, _conteos(session).get(marca.id, 0))


@router.get("/{slug}/logo")
def logo(slug: str, session: SessionDep, almacen: AlmacenDep) -> Response:
    """El logo de la marca. Sin autenticar: es la vitrina, como las fotos."""
    marca = _buscar(session, slug)
    if marca.logo_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{slug} no tiene logo")
    return responder_objeto(almacen, marca.logo_key)


@router.post("/{slug}/logo", response_model=MarcaOut)
def subir_logo(
    slug: str,
    session: SessionDep,
    usuario: EditorDep,
    almacen: AlmacenDep,
    archivo: Annotated[UploadFile, File(description="Logo de la marca")],
) -> MarcaOut:
    """Pone o reemplaza el logo. Para la marca que no está en el impreso y para cuando
    el recorte del extractor salió cortado."""
    marca = _buscar(session, slug)
    try:
        extension = validar_imagen_producto(archivo.content_type, archivo.size)
    except TipoNoPermitido as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except ArchivoDemasiadoGrande as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc

    marca.logo_key = almacen.guardar(
        key_logo_marca(marca.slug, extension),
        archivo.file,
        archivo.content_type or "application/octet-stream",
    )
    session.flush()
    return _a_salida(marca, _conteos(session).get(marca.id, 0))


@router.post("/{slug}/adoptar", response_model=ResultadoAdopcion)
def adoptar_logo(
    slug: str, adopcion: Adopcion, session: SessionDep, usuario: EditorDep
) -> ResultadoAdopcion:
    """Le da esta marca a todos los productos que llevan ese recorte.

    Es el paso que convierte una imagen anónima en una marca de verdad. Si la marca no
    tenía logo, adopta también la imagen: el logo del impreso es el logo del proveedor.

    **Sólo llena los vacíos.** Un producto que ya tiene marca no se toca, aunque su
    recorte coincida: dos páginas del catálogo traen el mismo logo cortado distinto, y
    adoptar el segundo no puede deshacer lo que alguien ya decidió sobre el primero.
    Por eso también un mismo nombre puede adoptar varios recortes, que es como se
    juntan los duplicados.
    """
    marca = _buscar(session, slug)
    productos = session.scalars(
        select(Producto).where(
            Producto.marca_logo_key == adopcion.logo_key,
            Producto.marca_id.is_(None),
        )
    ).all()
    if not productos:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Ningún producto sin marca usa el logo '{adopcion.logo_key}'",
        )
    for producto in productos:
        producto.marca_id = marca.id
    if marca.logo_key is None:
        marca.logo_key = adopcion.logo_key
    session.flush()
    return ResultadoAdopcion(
        marca=_a_salida(marca, _conteos(session).get(marca.id, 0)),
        productos_asignados=len(productos),
    )
