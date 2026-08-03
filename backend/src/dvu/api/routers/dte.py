"""Documentos tributarios electrónicos (Fase 2).

Emitir es irreversible: un folio entregado al SII no se borra, se corrige con nota de
crédito. Por eso todos los endpoints de emisión son de `admin` y ninguno acepta
"reintentar" a ciegas — si ya hay documento vigente, responden 409.
"""

from __future__ import annotations

import uuid as uuid_lib
from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.api.deps import SessionDep, UsuarioDep, exige_rol
from dvu.db.models import Dte, Pedido, Usuario
from dvu.domain.dte import NoFacturable
from dvu.facturacion import emitir_factura, emitir_guia, emitir_nota_credito
from dvu.integraciones.dte import ErrorDte

router = APIRouter(prefix="/dte", tags=["dte"])

AdminDep = Annotated[Usuario, Depends(exige_rol("admin"))]


class EmisionEntrada(BaseModel):
    numero_pedido: str


class NotaCreditoEntrada(EmisionEntrada):
    #: El SII exige razón de la anulación, y sin ella la nota queda impugnable.
    motivo: str = Field(min_length=3, max_length=255)


class DteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: uuid_lib.UUID
    tipo: int
    folio: int | None
    numero_pedido: str = ""
    rut_receptor: str
    neto_clp: int
    iva_clp: int
    total_clp: int
    estado: str
    track_id: str | None
    glosa_sii: str | None
    #: Momento de emisión, con hora: el SII ordena los documentos por fecha y hora.
    creado_en: datetime


@router.post("/facturas", response_model=DteOut, status_code=status.HTTP_201_CREATED)
def facturar(entrada: EmisionEntrada, session: SessionDep, usuario: AdminDep) -> DteOut:
    """Factura afecta tipo 33: el documento de venta de DVU. Nunca boleta — los clientes
    son ferreterías que descuentan IVA."""
    pedido = _pedido(session, entrada.numero_pedido)
    return _emitir(lambda: emitir_factura(session, pedido_id=pedido.id, usuario_id=usuario.id))


@router.post("/guias", response_model=DteOut, status_code=status.HTTP_201_CREATED)
def guia(entrada: EmisionEntrada, session: SessionDep, usuario: AdminDep) -> DteOut:
    """Guía de despacho tipo 52. El pedido no pasa a `despachado` sin ella."""
    pedido = _pedido(session, entrada.numero_pedido)
    return _emitir(lambda: emitir_guia(session, pedido_id=pedido.id, usuario_id=usuario.id))


@router.post("/notas-credito", response_model=DteOut, status_code=status.HTTP_201_CREATED)
def nota_credito(entrada: NotaCreditoEntrada, session: SessionDep, usuario: AdminDep) -> DteOut:
    """Nota de crédito tipo 61: anula una factura ya emitida y deja las dos en el
    registro."""
    pedido = _pedido(session, entrada.numero_pedido)
    return _emitir(
        lambda: emitir_nota_credito(
            session, pedido_id=pedido.id, motivo=entrada.motivo, usuario_id=usuario.id
        )
    )


@router.get("", response_model=list[DteOut])
def listar(
    session: SessionDep,
    usuario: UsuarioDep,
    numero_pedido: Annotated[str | None, Query()] = None,
    tipo: Annotated[int | None, Query(description="33, 52 o 61")] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[DteOut]:
    consulta = select(Dte, Pedido.numero).join(Pedido, Dte.pedido_id == Pedido.id)
    if usuario.rol == "vendedor":
        consulta = consulta.where(Pedido.vendedor_id == usuario.id)
    if numero_pedido is not None:
        consulta = consulta.where(Pedido.numero == numero_pedido)
    if tipo is not None:
        consulta = consulta.where(Dte.tipo == tipo)

    filas = session.execute(consulta.order_by(Dte.id.desc()).limit(limite)).all()
    return [_a_salida(dte, numero) for dte, numero in filas]


# --- apoyo -------------------------------------------------------------------


def _emitir(accion: Callable[[], Dte]) -> DteOut:
    """Traduce los errores de dominio a HTTP.

    `NoFacturable` es 409 y no 422: la petición está bien formada, lo que pasa es que el
    estado del pedido no admite ese documento.
    """
    try:
        dte = accion()
    except NoFacturable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ErrorDte as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return _a_salida(dte, dte.pedido.numero)


def _pedido(session: Session, numero: str) -> Pedido:
    pedido = session.scalar(select(Pedido).where(Pedido.numero == numero))
    if pedido is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe el pedido {numero}")
    return pedido


def _a_salida(dte: Dte, numero_pedido: str) -> DteOut:
    salida = DteOut.model_validate(dte)
    salida.numero_pedido = numero_pedido
    salida.neto_clp = int(dte.neto_clp)
    salida.iva_clp = int(dte.iva_clp)
    salida.total_clp = int(dte.total_clp)
    return salida
