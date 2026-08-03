"""Conciliación bancaria (Fase 2).

Reemplaza al dueño mirando la cartola del banco contra los comprobantes de WhatsApp.

Todo aquí es de `admin`: son los movimientos de la cuenta bancaria de la empresa. Un
vendedor declara pagos, pero no ve la cartola.

La bandeja (`GET /sugerencias`) es parte del diseño, no una carencia: la conciliación
nunca es 100 % automática y lo que no cuadra tiene que quedar a la vista de alguien.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from dvu.api.deps import SessionDep, exige_rol
from dvu.conciliacion import ESTADOS_CONCILIABLES, aplicar_coincidencia, sincronizar_y_conciliar
from dvu.db.models import Cliente, MovimientoBanco, Pago, Usuario
from dvu.integraciones.banco import ErrorBanco

router = APIRouter(prefix="/conciliacion", tags=["conciliacion"])

AdminDep = Annotated[Usuario, Depends(exige_rol("admin"))]


class RangoSync(BaseModel):
    #: Sin rango se usan los últimos `DVU_CONCILIACION_DIAS_ATRAS` días.
    desde: date | None = None
    hasta: date | None = None


class SugerenciaOut(BaseModel):
    pago_id: int
    movimiento_id_externo: str
    confianza: float
    motivos: list[str]


class ResumenOut(BaseModel):
    movimientos_nuevos: int
    movimientos_ya_conocidos: int
    conciliados: int
    para_revisar: int
    pagos_sin_respaldo: int
    sugerencias: list[SugerenciaOut]


class MovimientoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    fecha: date
    monto_clp: int
    descripcion: str | None
    referencia: str | None
    rut_contraparte: str | None
    estado: str


class PagoPendienteOut(BaseModel):
    id: int
    cliente_rut: str
    monto_clp: int
    fecha_pago: date
    referencia: str | None
    estado: str


class BandejaOut(BaseModel):
    """Los dos lados sin cruzar. Quien revisa necesita ver ambos para decidir."""

    pagos: list[PagoPendienteOut]
    movimientos: list[MovimientoOut]


class AplicarEntrada(BaseModel):
    pago_id: int = Field(gt=0)
    movimiento_id: int = Field(gt=0)


@router.post("/sincronizar", response_model=ResumenOut)
def sincronizar(rango: RangoSync, session: SessionDep, usuario: AdminDep) -> ResumenOut:
    """Trae la cartola y concilia lo que supere el umbral."""
    try:
        resumen = sincronizar_y_conciliar(session, desde=rango.desde, hasta=rango.hasta)
    except ErrorBanco as exc:
        # 502 y no 500: el que falló fue el agregador, no DVU. Y no se responde "0
        # movimientos", que haría parecer que ningún pago tiene respaldo.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return ResumenOut(
        movimientos_nuevos=resumen.movimientos_nuevos,
        movimientos_ya_conocidos=resumen.movimientos_ya_conocidos,
        conciliados=resumen.conciliados,
        para_revisar=resumen.para_revisar,
        pagos_sin_respaldo=resumen.pagos_sin_respaldo,
        sugerencias=[
            SugerenciaOut(
                pago_id=c.pago_id,
                movimiento_id_externo=c.movimiento_id_externo,
                confianza=c.confianza,
                motivos=list(c.motivos),
            )
            for c in resumen.sugerencias
        ],
    )


@router.get("/bandeja", response_model=BandejaOut)
def bandeja(
    session: SessionDep,
    usuario: AdminDep,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
) -> BandejaOut:
    """Lo que la máquina no resolvió sola."""
    filas = session.execute(
        select(Pago, Cliente.rut)
        .join(Cliente, Pago.cliente_id == Cliente.id)
        .where(Pago.estado.in_(ESTADOS_CONCILIABLES), Pago.movimiento_banco_id.is_(None))
        .order_by(Pago.fecha_pago.desc())
        .limit(limite)
    ).all()

    movimientos = session.scalars(
        select(MovimientoBanco)
        .where(MovimientoBanco.estado == "sin_conciliar", MovimientoBanco.monto_clp > 0)
        .order_by(MovimientoBanco.fecha.desc())
        .limit(limite)
    ).all()

    return BandejaOut(
        pagos=[
            PagoPendienteOut(
                id=pago.id,
                cliente_rut=rut,
                monto_clp=int(pago.monto_clp),
                fecha_pago=pago.fecha_pago,
                referencia=pago.referencia,
                estado=pago.estado,
            )
            for pago, rut in filas
        ],
        movimientos=[_a_salida(m) for m in movimientos],
    )


@router.post("/aplicar", response_model=PagoPendienteOut)
def aplicar(entrada: AplicarEntrada, session: SessionDep, usuario: AdminDep) -> PagoPendienteOut:
    """Confirma a mano un cruce de la bandeja."""
    try:
        pago = aplicar_coincidencia(
            session,
            pago_id=entrada.pago_id,
            movimiento_id=entrada.movimiento_id,
            usuario_id=usuario.id,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return PagoPendienteOut(
        id=pago.id,
        cliente_rut=pago.cliente.rut,
        monto_clp=int(pago.monto_clp),
        fecha_pago=pago.fecha_pago,
        referencia=pago.referencia,
        estado=pago.estado,
    )


@router.get("/movimientos", response_model=list[MovimientoOut])
def listar_movimientos(
    session: SessionDep,
    usuario: AdminDep,
    estado: Annotated[str | None, Query(description="sin_conciliar, conciliado, ignorado")] = None,
    desde: Annotated[date | None, Query()] = None,
    hasta: Annotated[date | None, Query()] = None,
    limite: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[MovimientoOut]:
    consulta = select(MovimientoBanco)
    if estado is not None:
        consulta = consulta.where(MovimientoBanco.estado == estado)
    if desde is not None:
        consulta = consulta.where(MovimientoBanco.fecha >= desde)
    if hasta is not None:
        consulta = consulta.where(MovimientoBanco.fecha <= hasta)

    filas = session.scalars(
        consulta.order_by(MovimientoBanco.fecha.desc(), MovimientoBanco.id.desc()).limit(limite)
    ).all()
    return [_a_salida(m) for m in filas]


@router.post("/movimientos/{movimiento_id}/ignorar", response_model=MovimientoOut)
def ignorar(movimiento_id: int, session: SessionDep, usuario: AdminDep) -> MovimientoOut:
    """Saca de la bandeja un abono que no es un pago de cliente (un aporte de capital,
    una devolución del banco). No se borra: se marca, y sigue auditable."""
    movimiento = session.get(MovimientoBanco, movimiento_id)
    if movimiento is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"No existe el movimiento {movimiento_id}"
        )
    if movimiento.estado == "conciliado":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="El movimiento ya respalda un pago verificado"
        )

    movimiento.estado = "ignorado"
    session.flush()
    return _a_salida(movimiento)


def _a_salida(movimiento: MovimientoBanco) -> MovimientoOut:
    salida = MovimientoOut.model_validate(movimiento)
    salida.monto_clp = int(movimiento.monto_clp)
    return salida
