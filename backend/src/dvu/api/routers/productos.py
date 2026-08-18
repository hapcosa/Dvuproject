"""Catálogo de productos.

La búsqueda está pensada para el vendedor en terreno: escribe «codo media» o pega
el código del proveedor que tenga a mano. Ambos caminos llegan al mismo producto.

La edición es sólo de `admin`. El catálogo se carga desde el PDF (`make cargar-catalogo`)
y esa sigue siendo la vía masiva; estos endpoints son para corregir a mano lo que el
extractor no pudo leer bien y para los productos que no están en el PDF impreso.
"""

from __future__ import annotations

import uuid as uuid_lib
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from dvu.almacenamiento import (
    Almacen,
    ArchivoDemasiadoGrande,
    TipoNoPermitido,
    get_almacen,
    key_imagen_producto,
    validar_imagen_producto,
)
from dvu.api.deps import exige_rol
from dvu.api.objetos import responder_objeto
from dvu.db.models import Categoria, Marca, Producto, ProductoAlias, Usuario
from dvu.db.session import get_session
from dvu.domain.roles import EDITOR

router = APIRouter(prefix="/productos", tags=["catálogo"])

SessionDep = Annotated[Session, Depends(get_session)]
#: Quien mantiene el catálogo. `exige_rol` deja pasar a `admin` siempre, así que esto
#: es «editor o administrador»: el catálogo lo cuida alguien que no tiene por qué ver
#: cobranza ni facturación, y antes la única forma de dejarlo editar era darle `admin`.
EditorDep = Annotated[Usuario, Depends(exige_rol(EDITOR))]
AlmacenDep = Annotated[Almacen, Depends(get_almacen)]


class ProductoOut(BaseModel):
    # `populate_by_name` porque `marca` se lee del modelo por `marca_nombre`: en el
    # modelo `marca` es la relación, acá es el nombre.
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    uuid: uuid_lib.UUID
    sku: str
    descripcion: str
    #: La marca que alguien nombró. `None` mientras su logo siga sin nombre.
    marca: str | None = Field(default=None, validation_alias="marca_nombre")
    marca_slug: str | None = None
    #: Si la marca tiene logo propio, está en `GET /marcas/{marca_slug}/logo`.
    #: Ese le gana al recorte del extractor: es el que se sube justamente
    #: cuando el recorte salió cortado.
    marca_tiene_logo: bool = False
    #: Lo que imprimía la columna «Marca» del PDF: casi siempre una medida mal
    #: clasificada. Se devuelve para poder corregirla, no para mostrarla como marca.
    marca_impresa: str | None = None
    medida: str | None
    unidad_venta: str
    #: Cantidad mínima y escalón de compra. El carrito valida contra este valor.
    multiplo_venta: int
    envase: str | None
    precio_lista_clp: int
    imagen_key: str | None
    #: El recorte del logo que hizo el extractor. Es el que muestra el catálogo
    #: mientras la marca no tenga nombre.
    marca_logo_key: str | None = None
    #: `None` es un valor legítimo: hay familias del catálogo sin regla de clasificación
    #: y no se les inventa una. Siguen apareciendo en la búsqueda por texto.
    categoria_slug: str | None = None
    categoria_nombre: str | None = None
    codigos_proveedor: list[str] = Field(default_factory=list)


class PaginaProductos(BaseModel):
    total: int
    items: list[ProductoOut]


class ProductoEntrada(BaseModel):
    """Alta manual. El precio y el múltiplo son obligatorios: son las dos cosas sin las
    cuales el vendedor no puede cotizar."""

    sku: str = Field(min_length=1, max_length=64)
    descripcion: str = Field(min_length=1)
    precio_lista_clp: int = Field(ge=0)
    #: Escalón de venta. DVU vende por caja: `1` sólo si el producto de verdad se vende
    #: suelto, no como valor por defecto cómodo.
    multiplo_venta: int = Field(default=1, ge=1)
    unidad_venta: str = "UNID"
    envase: str | None = None
    #: Slug de una marca ya creada (`POST /marcas`). No se crea al vuelo: una marca
    #: nueva por cada tipeo distinto es justamente lo que dejó el catálogo con 220
    #: logos anónimos.
    marca_slug: str | None = None
    medida: str | None = None
    codigos_proveedor: list[str] = Field(default_factory=list)


class ProductoParche(BaseModel):
    """Corrección puntual. Lo que no viene no se toca.

    `extra="forbid"` porque el modo de falla natural de un parche es el peor: un campo
    con el nombre equivocado se ignora, la API responde 200 y el editor se va creyendo
    que guardó. Pasó al renombrar `marca`: la página seguía mandando el nombre viejo y
    la respuesta no decía nada. Mejor un 422 que nombre el campo.
    """

    model_config = ConfigDict(extra="forbid")

    descripcion: str | None = Field(default=None, min_length=1)
    precio_lista_clp: int | None = Field(default=None, ge=0)
    multiplo_venta: int | None = Field(default=None, ge=1)
    unidad_venta: str | None = None
    envase: str | None = None
    #: Slug de la marca, o `null` para sacarla.
    marca_slug: str | None = None
    medida: str | None = None
    activo: bool | None = None
    #: Slug de la categoría, o `null` para sacarla. La corrección a mano manda: la
    #: clasificación automática (`make clasificar`) no vuelve a pisar lo que se asignó
    #: acá, sólo toca los productos que quedaron sin categoría.
    categoria_slug: str | None = None


@router.get("", response_model=PaginaProductos)
def listar(
    session: SessionDep,
    q: Annotated[str | None, Query(description="Texto libre o código de proveedor")] = None,
    categoria: Annotated[str | None, Query(description="Slug de la categoría")] = None,
    sin_categoria: Annotated[
        bool, Query(description="Sólo lo que ninguna regla clasificó, para revisarlo")
    ] = False,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginaProductos:
    consulta = select(Producto).where(Producto.activo.is_(True))

    if sin_categoria:
        consulta = consulta.where(Producto.categoria_id.is_(None))
    elif categoria:
        # Una categoría que no existe devuelve vacío, no 404: el filtro llega desde un
        # enlace o un marcador y romper la página entera por eso no ayuda a nadie.
        consulta = consulta.where(
            Producto.categoria_id.in_(select(Categoria.id).where(Categoria.slug == categoria))
        )

    if q:
        termino = f"%{q.strip()}%"
        # Coincidencia por descripción o por cualquier código de proveedor: en el
        # catálogo conviven cinco formatos de código distintos.
        subconsulta = select(ProductoAlias.producto_id).where(ProductoAlias.codigo.ilike(termino))
        consulta = consulta.where(
            or_(
                Producto.descripcion.ilike(termino),
                Producto.sku.ilike(termino),
                Producto.id.in_(subconsulta),
            )
        )

    total = session.scalar(select(func.count()).select_from(consulta.subquery())) or 0

    filas = session.scalars(
        consulta.options(selectinload(Producto.alias))
        .order_by(Producto.descripcion)
        .limit(limite)
        .offset(offset)
    ).all()

    return PaginaProductos(total=total, items=[_a_salida(p) for p in filas])


@router.get("/{sku}", response_model=ProductoOut)
def obtener(sku: str, session: SessionDep) -> ProductoOut:
    producto = session.scalar(
        select(Producto).options(selectinload(Producto.alias)).where(Producto.sku == sku)
    )
    if producto is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe el producto {sku}")
    return _a_salida(producto)


@router.get("/{sku}/imagen")
def imagen(sku: str, session: SessionDep, almacen: AlmacenDep) -> Response:
    """Devuelve la foto del producto.

    A diferencia del comprobante de pago, la foto de catálogo no tiene nada reservado:
    es la misma que está impresa en el PDF. Sale por la API y no por URL firmada para que
    se vea igual desde la LAN y desde la VPN — ver `dvu.api.objetos`.
    """
    producto = _buscar(session, sku)
    if producto.imagen_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{sku} no tiene imagen")
    return responder_objeto(almacen, producto.imagen_key)


@router.get("/{sku}/marca")
def logo_de_marca(sku: str, session: SessionDep, almacen: AlmacenDep) -> Response:
    """Devuelve el logo del proveedor recortado del catálogo impreso.

    Va como endpoint propio y no como URL firmada dentro del listado porque la firma dura
    cinco minutos y la página del catálogo se deja abierta toda la mañana en el mesón.
    """
    producto = _buscar(session, sku)
    if producto.marca_logo_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{sku} no tiene logo de marca")
    return responder_objeto(almacen, producto.marca_logo_key)


@router.post("", response_model=ProductoOut, status_code=status.HTTP_201_CREATED)
def crear(entrada: ProductoEntrada, session: SessionDep, usuario: EditorDep) -> ProductoOut:
    """Alta manual de un producto que no viene en el PDF."""
    producto = Producto(
        sku=entrada.sku.strip(),
        descripcion=entrada.descripcion.strip(),
        precio_lista_clp=entrada.precio_lista_clp,
        multiplo_venta=entrada.multiplo_venta,
        unidad_venta=entrada.unidad_venta,
        envase=entrada.envase,
        marca=_resolver_marca(session, entrada.marca_slug),
        medida=entrada.medida,
    )
    producto.alias = [
        ProductoAlias(codigo=codigo.strip(), origen="manual")
        for codigo in entrada.codigos_proveedor
        if codigo.strip()
    ]
    session.add(producto)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Ya existe un producto con SKU {entrada.sku}"
        ) from exc
    return _a_salida(producto)


@router.post("/{sku}/imagen", response_model=ProductoOut)
def subir_imagen(
    sku: str,
    session: SessionDep,
    usuario: EditorDep,
    almacen: AlmacenDep,
    archivo: Annotated[UploadFile, File(description="Foto del producto")],
) -> ProductoOut:
    """Reemplaza la foto del producto.

    La vía masiva sigue siendo el PDF (`cargar-catalogo --con-imagenes`). Esto es para
    el producto que no está impreso y para cuando el recorte del extractor salió mal:
    sin esto, una foto equivocada sólo se arregla volviendo a correr la extracción
    completa, que son varias horas.
    """
    producto = _buscar(session, sku)
    try:
        extension = validar_imagen_producto(archivo.content_type, archivo.size)
    except TipoNoPermitido as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except ArchivoDemasiadoGrande as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc

    producto.imagen_key = almacen.guardar(
        key_imagen_producto(producto.sku, extension),
        archivo.file,
        archivo.content_type or "application/octet-stream",
    )
    session.flush()
    return _a_salida(producto)


@router.patch("/{sku}", response_model=ProductoOut)
def actualizar(
    sku: str, parche: ProductoParche, session: SessionDep, usuario: EditorDep
) -> ProductoOut:
    """Corrige la ficha. `activo=false` la saca del catálogo sin borrar la fila: puede
    estar referenciada en pedidos históricos."""
    producto = _buscar(session, sku)
    cambios = parche.model_dump(exclude_unset=True)

    if "marca_slug" in cambios:
        producto.marca = _resolver_marca(session, cambios.pop("marca_slug"))

    if "categoria_slug" in cambios:
        # Se asigna la relación y no `categoria_id`: la ficha que se devuelve sale de
        # `producto.categoria`, y tocando sólo el id quedaría mostrando la categoría
        # anterior hasta la próxima consulta.
        producto.categoria = _resolver_categoria(session, cambios.pop("categoria_slug"))

    for campo, valor in cambios.items():
        setattr(producto, campo, valor)
    session.flush()
    return _a_salida(producto)


def _resolver_marca(session: Session, slug: str | None) -> Marca | None:
    if slug is None:
        return None
    marca = session.scalar(select(Marca).where(Marca.slug == slug))
    if marca is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe la marca {slug}")
    return marca


def _resolver_categoria(session: Session, slug: str | None) -> Categoria | None:
    if slug is None:
        return None
    categoria = session.scalar(select(Categoria).where(Categoria.slug == slug))
    if categoria is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe la categoría {slug}")
    return categoria


@router.post("/{sku}/alias", response_model=ProductoOut, status_code=status.HTTP_201_CREATED)
def agregar_alias(sku: str, codigo: str, session: SessionDep, usuario: EditorDep) -> ProductoOut:
    """Suma un código de proveedor. El vendedor busca por el que tenga a mano."""
    producto = _buscar(session, sku)
    limpio = codigo.strip()
    if not limpio:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="El código viene vacío")
    if any(a.codigo == limpio for a in producto.alias):
        return _a_salida(producto)

    producto.alias.append(ProductoAlias(codigo=limpio, origen="manual"))
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"El código {limpio} ya está en uso"
        ) from exc
    return _a_salida(producto)


def _buscar(session: Session, sku: str) -> Producto:
    producto = session.scalar(
        select(Producto).options(selectinload(Producto.alias)).where(Producto.sku == sku)
    )
    if producto is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe el producto {sku}")
    return producto


def _a_salida(producto: Producto) -> ProductoOut:
    salida = ProductoOut.model_validate(producto)
    salida.codigos_proveedor = [a.codigo for a in producto.alias]
    if producto.categoria is not None:
        salida.categoria_slug = producto.categoria.slug
        salida.categoria_nombre = producto.categoria.nombre
    if producto.marca is not None:
        salida.marca_slug = producto.marca.slug
        salida.marca_tiene_logo = producto.marca.logo_key is not None
    return salida
