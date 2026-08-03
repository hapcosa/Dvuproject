"""Catálogo de productos.

La búsqueda está pensada para el vendedor en terreno: escribe «codo media» o pega
el código del proveedor que tenga a mano. Ambos caminos llegan al mismo producto.
"""

from __future__ import annotations

import uuid as uuid_lib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from dvu.db.models import Producto, ProductoAlias
from dvu.db.session import get_session

router = APIRouter(prefix="/productos", tags=["catálogo"])

SessionDep = Annotated[Session, Depends(get_session)]


class ProductoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: uuid_lib.UUID
    sku: str
    descripcion: str
    marca: str | None
    medida: str | None
    unidad_venta: str
    #: Cantidad mínima y escalón de compra. El carrito valida contra este valor.
    multiplo_venta: int
    envase: str | None
    precio_lista_clp: int
    imagen_key: str | None
    codigos_proveedor: list[str] = Field(default_factory=list)


class PaginaProductos(BaseModel):
    total: int
    items: list[ProductoOut]


@router.get("", response_model=PaginaProductos)
def listar(
    session: SessionDep,
    q: Annotated[str | None, Query(description="Texto libre o código de proveedor")] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginaProductos:
    consulta = select(Producto).where(Producto.activo.is_(True))

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


def _a_salida(producto: Producto) -> ProductoOut:
    salida = ProductoOut.model_validate(producto)
    salida.codigos_proveedor = [a.codigo for a in producto.alias]
    return salida
