"""Categorías del catálogo.

El árbol no viene del PDF —sus páginas no traen encabezado de sección— sino de las
reglas de `dvu.domain.categorias`, que `make clasificar` aplica sobre la descripción.
Estos endpoints son para navegarlo y para que el administrador lo corrija.

Se lee sin autenticar, igual que el catálogo: es la vitrina. Editar es sólo de `admin`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dvu.api.deps import exige_rol
from dvu.db.models import Categoria, Producto, Usuario
from dvu.db.session import get_session
from dvu.domain.roles import EDITOR

router = APIRouter(prefix="/categorias", tags=["catálogo"])

SessionDep = Annotated[Session, Depends(get_session)]
#: Quien mantiene el catálogo. `exige_rol` deja pasar a `admin` siempre, así que esto
#: es «editor o administrador»: el catálogo lo cuida alguien que no tiene por qué ver
#: cobranza ni facturación, y antes la única forma de dejarlo editar era darle `admin`.
EditorDep = Annotated[Usuario, Depends(exige_rol(EDITOR))]


class CategoriaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    nombre: str
    orden: int
    #: Cuántos productos activos cuelgan de ella. Una categoría vacía en el menú es una
    #: promesa que el catálogo no cumple: el vendedor entra y no hay nada.
    productos: int = 0


class CategoriaEntrada(BaseModel):
    slug: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9-]+$")
    nombre: str = Field(min_length=1, max_length=128)
    orden: int = 0


class CategoriaParche(BaseModel):
    """Renombrar y reordenar. El slug no se toca: es lo que apunta desde los enlaces."""

    nombre: str | None = Field(default=None, min_length=1, max_length=128)
    orden: int | None = None


@router.get("", response_model=list[CategoriaOut])
def listar(
    session: SessionDep,
    con_vacias: Annotated[bool, Query(description="Incluye las que no tienen productos")] = False,
) -> list[CategoriaOut]:
    conteos = dict(
        session.execute(
            select(Producto.categoria_id, func.count())
            .where(Producto.activo.is_(True))
            .group_by(Producto.categoria_id)
        )
        .tuples()
        .all()
    )

    filas = session.scalars(select(Categoria).order_by(Categoria.orden, Categoria.nombre)).all()
    salida = [
        CategoriaOut(slug=c.slug, nombre=c.nombre, orden=c.orden, productos=conteos.get(c.id, 0))
        for c in filas
    ]
    return salida if con_vacias else [c for c in salida if c.productos]


@router.post("", response_model=CategoriaOut, status_code=status.HTTP_201_CREATED)
def crear(entrada: CategoriaEntrada, session: SessionDep, admin: EditorDep) -> CategoriaOut:
    categoria = Categoria(slug=entrada.slug, nombre=entrada.nombre, orden=entrada.orden)
    session.add(categoria)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Ya existe la categoría {entrada.slug}"
        ) from exc
    return CategoriaOut.model_validate(categoria)


@router.patch("/{slug}", response_model=CategoriaOut)
def actualizar(
    slug: str, parche: CategoriaParche, session: SessionDep, admin: EditorDep
) -> CategoriaOut:
    categoria = session.scalar(select(Categoria).where(Categoria.slug == slug))
    if categoria is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe la categoría {slug}")

    for campo, valor in parche.model_dump(exclude_unset=True).items():
        setattr(categoria, campo, valor)
    session.flush()
    return CategoriaOut.model_validate(categoria)
