"""Registro de comprobantes de transferencia.

Orquesta lo que el dominio decide: clasifica, busca duplicados contra lo ya guardado y
persiste. La regla que sostiene el módulo es que **nada se rechaza**: un comprobante
incompleto se guarda marcado, porque el aviso del vendedor es el dato escaso y perderlo
es el único error irreparable de este flujo.
"""

from __future__ import annotations

import uuid as uuid_lib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.db.models import Cliente, Comprobante
from dvu.domain.comprobante import (
    DatosComprobante,
    EstadoComprobante,
    clasificar,
    clave_duplicado,
)
from dvu.domain.rut import RutInvalido, normalizar


@dataclass(slots=True)
class Entrada:
    """Lo que llega del formulario del vendedor. Todo opcional salvo el vendedor."""

    vendedor_id: int
    cliente_texto: str = ""
    cliente_rut: str | None = None
    facturas: tuple[str, ...] = ()
    monto_clp: int | None = None
    banco: str | None = None
    rut_contraparte: str | None = None
    numero_operacion: str | None = None
    fecha_transferencia: date | None = None
    detalle: str = ""
    client_uuid: uuid_lib.UUID | None = None


def registrar(session: Session, entrada: Entrada) -> Comprobante:
    """Guarda el comprobante y lo deja clasificado.

    Idempotente por `client_uuid`: el mismo envío repetido devuelve la fila existente
    sin tocarla. La app del vendedor reintenta al recuperar señal y no puede saber si el
    primer intento llegó.
    """
    if entrada.client_uuid is not None:
        existente = session.scalar(
            select(Comprobante).where(Comprobante.client_uuid == entrada.client_uuid)
        )
        if existente is not None:
            return existente

    cliente = _buscar_cliente(session, entrada)
    # El nombre del cliente identificado gana al texto tipeado para clasificar: si el
    # RUT calzó, el cliente está identificado aunque el vendedor haya escrito un apodo.
    nombre = cliente.razon_social if cliente is not None else entrada.cliente_texto

    datos = DatosComprobante(
        cliente=nombre,
        facturas=entrada.facturas,
        monto_clp=entrada.monto_clp,
        numero_operacion=entrada.numero_operacion,
        fecha_transferencia=entrada.fecha_transferencia,
        detalle=entrada.detalle,
    )
    clasificacion = clasificar(datos)

    estado = clasificacion.estado
    observacion = clasificacion.observacion
    if (previo := _duplicado_de(session, datos)) is not None:
        estado = EstadoComprobante.DUPLICADO_POSIBLE
        aviso = f"Posible duplicado del comprobante {previo.uuid}"
        observacion = f"{observacion} | {aviso}" if observacion else aviso

    comprobante = Comprobante(
        client_uuid=entrada.client_uuid,
        vendedor_id=entrada.vendedor_id,
        cliente_id=cliente.id if cliente is not None else None,
        cliente_texto=entrada.cliente_texto.strip(),
        facturas=list(entrada.facturas),
        monto_clp=Decimal(entrada.monto_clp) if entrada.monto_clp is not None else None,
        banco=entrada.banco,
        rut_contraparte=entrada.rut_contraparte,
        numero_operacion=entrada.numero_operacion,
        fecha_transferencia=entrada.fecha_transferencia,
        detalle=entrada.detalle,
        estado=estado.value,
        observacion=observacion,
    )
    session.add(comprobante)
    session.flush()
    return comprobante


def marcar_ingresado(session: Session, comprobante: Comprobante, usuario_id: int) -> Comprobante:
    """Cobranza ya lo pasó al sistema contable. No se borra: sale de la bandeja."""
    comprobante.ingresado = True
    comprobante.ingresado_por = usuario_id
    session.flush()
    return comprobante


def _buscar_cliente(session: Session, entrada: Entrada) -> Cliente | None:
    """Identifica al cliente por RUT si vino uno válido.

    Un RUT mal escrito no invalida el comprobante: se ignora y el comprobante queda con
    el cliente sin identificar, que es exactamente lo que pasó. Rechazarlo perdería el
    aviso completo por un dígito.
    """
    if not entrada.cliente_rut:
        return None
    try:
        rut = normalizar(entrada.cliente_rut)
    except RutInvalido:
        return None
    return session.scalar(select(Cliente).where(Cliente.rut == rut))


def _duplicado_de(session: Session, datos: DatosComprobante) -> Comprobante | None:
    """Busca un comprobante previo con la misma transferencia.

    La comparación exige nº de operación (ver `clave_duplicado`). Sin él no se marca
    nada: una alarma que suena todos los días deja de mirarse.
    """
    clave = clave_duplicado(datos)
    if clave is None:
        return None
    return session.scalar(
        select(Comprobante)
        .where(
            Comprobante.numero_operacion == clave.numero_operacion,
            Comprobante.monto_clp == Decimal(clave.monto_clp),
        )
        .order_by(Comprobante.id)
    )
