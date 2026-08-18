"""Pagos.

Reemplaza el flujo actual: el vendedor manda la foto de la transferencia por WhatsApp
y el dueño la contrasta a mano con la cartola.

Aquí el pago se declara con su comprobante y queda `declarado`. La verificación
automática contra la cartola es Fase 2; mientras tanto la hace una persona, pero ya
sobre datos estructurados y con la aplicación a pedidos registrada.

Regla que no cambia ni en Fase 2: **un pago nunca se descarta**. Si no cuadra, va a
`pendiente_revision`, que es la bandeja de excepciones.
"""

from __future__ import annotations

import uuid as uuid_lib
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from dvu.almacenamiento import (
    Almacen,
    ArchivoDemasiadoGrande,
    TipoNoPermitido,
    get_almacen,
    key_comprobante,
    validar_comprobante,
)
from dvu.api.deps import SessionDep, UsuarioDep, exige_rol
from dvu.db.models import Cliente, Pago, PagoAplicacion, Pedido, Usuario
from dvu.domain.roles import ADMIN, VENDEDOR

router = APIRouter(prefix="/pagos", tags=["pagos"])

#: Como dependencia y no como llamada directa: así los tests corren sin MinIO.
AlmacenDep = Annotated[Almacen, Depends(get_almacen)]

METODOS = frozenset({"transferencia", "efectivo", "cheque", "tarjeta"})

#: Estados terminales de la revisión humana. Sólo `admin` puede fijarlos.
ESTADOS_VERIFICACION = frozenset({"verificado", "rechazado", "pendiente_revision"})


class AplicacionEntrada(BaseModel):
    numero_pedido: str
    monto_clp: int = Field(gt=0)


class PagoEntrada(BaseModel):
    cliente_rut: str
    monto_clp: int = Field(gt=0)
    fecha_pago: date
    metodo: str = "transferencia"
    #: Nº de operación de la transferencia. Es lo que permitirá el match automático
    #: contra la cartola en Fase 2.
    referencia: str | None = None
    aplicaciones: list[AplicacionEntrada] = Field(default_factory=list)


class AplicacionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pedido_id: int
    monto_clp: int


class PagoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: uuid_lib.UUID
    cliente_rut: str = ""
    monto_clp: int
    fecha_pago: date
    metodo: str
    referencia: str | None
    estado: str
    comprobante_key: str | None
    aplicaciones: list[AplicacionOut]


class PaginaPagos(BaseModel):
    total: int
    items: list[PagoOut]


class CambioEstadoPago(BaseModel):
    estado: str
    motivo: str | None = None


@router.post("", response_model=PagoOut, status_code=status.HTTP_201_CREATED)
def declarar(
    entrada: PagoEntrada,
    session: SessionDep,
    usuario: Annotated[Usuario, Depends(exige_rol(VENDEDOR))],
) -> PagoOut:
    if entrada.metodo not in METODOS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Método inválido; se aceptan: {', '.join(sorted(METODOS))}",
        )

    cliente = session.scalar(select(Cliente).where(Cliente.rut == entrada.cliente_rut))
    if cliente is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"No existe el cliente {entrada.cliente_rut}"
        )

    aplicaciones = _resolver_aplicaciones(entrada, cliente, session)

    pago = Pago(
        cliente_id=cliente.id,
        monto_clp=Decimal(entrada.monto_clp),
        fecha_pago=entrada.fecha_pago,
        metodo=entrada.metodo,
        referencia=entrada.referencia,
        estado="declarado",
        registrado_por=usuario.id,
        aplicaciones=aplicaciones,
    )
    session.add(pago)
    session.flush()
    return _a_salida(pago, cliente)


@router.post("/{pago_uuid}/comprobante", response_model=PagoOut)
def subir_comprobante(
    pago_uuid: uuid_lib.UUID,
    session: SessionDep,
    usuario: Annotated[Usuario, Depends(exige_rol(VENDEDOR))],
    almacen: AlmacenDep,
    archivo: Annotated[UploadFile, File(description="Foto o PDF de la transferencia")],
) -> PagoOut:
    """Sube la foto de la transferencia. Reemplaza la que hoy va por WhatsApp."""
    pago = _buscar(pago_uuid, session)

    try:
        extension = validar_comprobante(archivo.content_type, archivo.size)
    except TipoNoPermitido as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except ArchivoDemasiadoGrande as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc

    key = key_comprobante(pago.uuid, extension)
    almacen.guardar(key, archivo.file, archivo.content_type or "application/octet-stream")
    pago.comprobante_key = key
    session.flush()
    return _a_salida(pago, pago.cliente)


@router.get("/{pago_uuid}/comprobante")
def ver_comprobante(
    pago_uuid: uuid_lib.UUID, session: SessionDep, usuario: UsuarioDep, almacen: AlmacenDep
) -> Response:
    """Redirige a una URL firmada de vida corta.

    El comprobante lleva datos bancarios del cliente: no se sirve desde una ruta
    pública ni con un enlace permanente.
    """
    pago = _buscar(pago_uuid, session)
    # Quien lo registró, o la administración. Sin esto bastaba tener sesión de cualquier
    # rol para pedir la foto de la transferencia de cualquier cliente, que es justo lo
    # que dice arriba que no pasa.
    if usuario.rol != ADMIN and pago.registrado_por != usuario.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No es tu pago")
    if pago.comprobante_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="El pago no tiene comprobante")

    url = almacen.url_firmada(pago.comprobante_key)
    return Response(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": url})


@router.get("", response_model=PaginaPagos)
def listar(
    session: SessionDep,
    # Vendedor —los suyos, ver abajo— o administración. Antes bastaba tener sesión: la
    # consulta sólo se acotaba para el rol `vendedor`, así que cualquier otro rol veía
    # todos los pagos con su monto y su cliente.
    #
    usuario: Annotated[Usuario, Depends(exige_rol(VENDEDOR))],
    estado: Annotated[str | None, Query(description="declarado, verificado, …")] = None,
    cliente_rut: Annotated[str | None, Query()] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginaPagos:
    consulta = select(Pago)
    if usuario.rol == VENDEDOR:
        consulta = consulta.where(Pago.registrado_por == usuario.id)
    if estado is not None:
        consulta = consulta.where(Pago.estado == estado)
    if cliente_rut is not None:
        consulta = consulta.join(Cliente).where(Cliente.rut == cliente_rut)

    total = session.scalar(select(func.count()).select_from(consulta.subquery())) or 0
    pagos = session.scalars(
        consulta.options(selectinload(Pago.aplicaciones), selectinload(Pago.cliente))
        .order_by(Pago.id.desc())
        .limit(limite)
        .offset(offset)
    ).all()

    return PaginaPagos(total=total, items=[_a_salida(p, p.cliente) for p in pagos])


@router.post("/{pago_uuid}/estado", response_model=PagoOut)
def cambiar_estado(
    pago_uuid: uuid_lib.UUID,
    cambio: CambioEstadoPago,
    session: SessionDep,
    usuario: Annotated[Usuario, Depends(exige_rol(ADMIN))],
) -> PagoOut:
    """Verificación manual. En Fase 2 la propone la conciliación; la última palabra
    sigue siendo humana."""
    if cambio.estado not in ESTADOS_VERIFICACION:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Estado inválido; se aceptan: {', '.join(sorted(ESTADOS_VERIFICACION))}",
        )

    pago = _buscar(pago_uuid, session)
    pago.estado = cambio.estado
    pago.verificado_por = usuario.id
    session.flush()
    return _a_salida(pago, pago.cliente)


# --- apoyo -------------------------------------------------------------------


def _resolver_aplicaciones(
    entrada: PagoEntrada, cliente: Cliente, session: Session
) -> list[PagoAplicacion]:
    """Un pago puede cubrir varios pedidos, pero nunca más que su propio monto."""
    if not entrada.aplicaciones:
        return []

    numeros = [a.numero_pedido for a in entrada.aplicaciones]
    pedidos = {
        p.numero: p
        for p in session.scalars(
            select(Pedido).where(Pedido.numero.in_(numeros), Pedido.cliente_id == cliente.id)
        )
    }

    faltantes = sorted(set(numeros) - set(pedidos))
    if faltantes:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Pedidos inexistentes o de otro cliente: {', '.join(faltantes)}",
        )

    total_aplicado = sum(a.monto_clp for a in entrada.aplicaciones)
    if total_aplicado > entrada.monto_clp:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(f"Se aplican {total_aplicado} CLP de un pago de {entrada.monto_clp} CLP"),
        )

    return [
        PagoAplicacion(
            pedido_id=pedidos[a.numero_pedido].id,
            monto_clp=Decimal(a.monto_clp),
        )
        for a in entrada.aplicaciones
    ]


def _buscar(pago_uuid: uuid_lib.UUID, session: Session) -> Pago:
    pago = session.scalar(
        select(Pago)
        .options(selectinload(Pago.aplicaciones), selectinload(Pago.cliente))
        .where(Pago.uuid == pago_uuid)
    )
    if pago is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe el pago {pago_uuid}")
    return pago


def _a_salida(pago: Pago, cliente: Cliente) -> PagoOut:
    salida = PagoOut.model_validate(pago)
    salida.cliente_rut = cliente.rut
    return salida
