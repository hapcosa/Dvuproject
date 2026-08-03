"""Clientes: las ferreterías que compran.

El RUT se valida y normaliza aquí, en el borde: un RUT mal guardado se convierte
más tarde en un DTE rechazado por el SII, y para entonces la venta ya ocurrió.

Un cliente nunca se borra. Tiene pedidos y pagos colgando, y el histórico es lo que
alimenta el Excel del dueño y la contabilidad: se desactiva (`activo=False`).
"""

from __future__ import annotations

import uuid as uuid_lib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dvu.api.deps import SessionDep, UsuarioDep, exige_rol
from dvu.db.models import Cliente, Usuario
from dvu.domain.rut import RutInvalido, normalizar

router = APIRouter(prefix="/clientes", tags=["clientes"])

CONDICIONES_PAGO = frozenset({"contado", "credito_15", "credito_30", "credito_60"})


class ClienteEntrada(BaseModel):
    rut: str
    razon_social: str = Field(min_length=1, max_length=255)
    nombre_fantasia: str | None = None
    giro: str | None = None
    direccion: str | None = None
    comuna: str | None = None
    ciudad: str | None = None
    #: Casilla a la que el SII envía el DTE. Sin ella la factura no llega.
    email_dte: str | None = None
    telefono: str | None = None
    condicion_pago: str = "contado"


class ClienteParche(BaseModel):
    """Todo opcional: el vendedor corrige un teléfono sin reenviar la ficha entera."""

    razon_social: str | None = Field(default=None, min_length=1, max_length=255)
    nombre_fantasia: str | None = None
    giro: str | None = None
    direccion: str | None = None
    comuna: str | None = None
    ciudad: str | None = None
    email_dte: str | None = None
    telefono: str | None = None
    condicion_pago: str | None = None
    activo: bool | None = None


class ClienteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: uuid_lib.UUID
    rut: str
    razon_social: str
    nombre_fantasia: str | None
    giro: str | None
    direccion: str | None
    comuna: str | None
    ciudad: str | None
    email_dte: str | None
    telefono: str | None
    condicion_pago: str
    activo: bool


class PaginaClientes(BaseModel):
    total: int
    items: list[ClienteOut]


@router.post("", response_model=ClienteOut, status_code=status.HTTP_201_CREATED)
def crear(
    entrada: ClienteEntrada,
    session: SessionDep,
    usuario: Annotated[Usuario, Depends(exige_rol("vendedor"))],
) -> ClienteOut:
    """Alta de ferretería. El vendedor queda asignado como responsable."""
    rut = _normalizar_rut(entrada.rut)
    _validar_condicion(entrada.condicion_pago)

    datos = entrada.model_dump(exclude={"rut"})
    cliente = Cliente(rut=rut, vendedor_id=usuario.id, **datos)

    # Savepoint: si dos vendedores dan de alta la misma ferretería a la vez, el choque
    # se revierte solo hasta aquí y no arrastra el resto de la transacción.
    try:
        with session.begin_nested():
            session.add(cliente)
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Ya existe un cliente con RUT {rut}"
        ) from exc

    return ClienteOut.model_validate(cliente)


@router.get("", response_model=PaginaClientes)
def listar(
    session: SessionDep,
    usuario: UsuarioDep,
    q: Annotated[str | None, Query(description="RUT o razón social")] = None,
    incluir_inactivos: Annotated[bool, Query()] = False,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginaClientes:
    consulta = select(Cliente)
    # Cada vendedor ve su cartera; bodega y admin ven todo.
    if usuario.rol == "vendedor":
        consulta = consulta.where(Cliente.vendedor_id == usuario.id)
    if not incluir_inactivos:
        consulta = consulta.where(Cliente.activo.is_(True))
    if q:
        termino = f"%{q.strip()}%"
        consulta = consulta.where(
            or_(Cliente.rut.ilike(termino), Cliente.razon_social.ilike(termino))
        )

    total = session.scalar(select(func.count()).select_from(consulta.subquery())) or 0
    filas = session.scalars(
        consulta.order_by(Cliente.razon_social).limit(limite).offset(offset)
    ).all()

    return PaginaClientes(total=total, items=[ClienteOut.model_validate(c) for c in filas])


@router.get("/{rut}", response_model=ClienteOut)
def obtener(rut: str, session: SessionDep, usuario: UsuarioDep) -> ClienteOut:
    return ClienteOut.model_validate(_buscar(rut, session, usuario))


@router.patch("/{rut}", response_model=ClienteOut)
def actualizar(
    rut: str,
    parche: ClienteParche,
    session: SessionDep,
    usuario: Annotated[Usuario, Depends(exige_rol("vendedor"))],
) -> ClienteOut:
    cliente = _buscar(rut, session, usuario)

    cambios = parche.model_dump(exclude_unset=True)
    if "condicion_pago" in cambios:
        _validar_condicion(cambios["condicion_pago"])
    # `activo=False` es la única forma de sacar un cliente de circulación: los
    # pedidos y pagos históricos tienen que seguir existiendo.
    for campo, valor in cambios.items():
        setattr(cliente, campo, valor)

    session.flush()
    return ClienteOut.model_validate(cliente)


# --- apoyo -------------------------------------------------------------------


def _normalizar_rut(rut: str) -> str:
    try:
        return normalizar(rut)
    except RutInvalido as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


def _validar_condicion(condicion: str) -> None:
    if condicion not in CONDICIONES_PAGO:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Condición inválida; se aceptan: {', '.join(sorted(CONDICIONES_PAGO))}",
        )


def _buscar(rut: str, session: Session, usuario: Usuario) -> Cliente:
    cliente = session.scalar(select(Cliente).where(Cliente.rut == _normalizar_rut(rut)))
    if cliente is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe el cliente {rut}")
    if usuario.rol == "vendedor" and cliente.vendedor_id != usuario.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="El cliente pertenece a otro vendedor"
        )
    return cliente
