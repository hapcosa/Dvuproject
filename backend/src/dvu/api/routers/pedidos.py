"""Pedidos.

Dos cosas gobiernan este módulo, y las dos vienen de cómo se trabaja en terreno:

1. **Idempotencia.** La app del vendedor arma el pedido sin señal y lo reenvía hasta
   recibir confirmación. El `client_uuid` que genera el dispositivo es la clave: un
   reenvío devuelve el mismo pedido con 200, no crea uno nuevo ni falla.
2. **Venta por múltiplos.** Una cantidad que no es múltiplo del envase se rechaza con
   el detalle de qué línea y qué cantidad sí sirve. No se corrige en silencio: el
   vendedor tiene que ver el cambio antes de comprometerlo con el cliente.

El precio se congela al crear el pedido. Si el catálogo cambia mañana, el pedido de
hoy sigue diciendo lo que el cliente aceptó.
"""

from __future__ import annotations

import uuid as uuid_lib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from dvu.api.deps import SessionDep, UsuarioDep, exige_rol
from dvu.config import get_settings
from dvu.db.models import Cliente, Pedido, PedidoEvento, PedidoLinea, Producto, Usuario
from dvu.db.numeracion import siguiente_numero
from dvu.domain.pedido import (
    CantidadInvalida,
    EstadoPedido,
    Linea,
    TransicionInvalida,
    ajustar_al_multiplo,
    calcular_totales,
    transicionar,
    validar_cantidad,
)
from dvu.facturacion import tiene_guia

router = APIRouter(prefix="/pedidos", tags=["pedidos"])


# --- esquemas ----------------------------------------------------------------


class LineaEntrada(BaseModel):
    sku: str
    cantidad: int = Field(gt=0)


class PedidoEntrada(BaseModel):
    #: Generado por el dispositivo. Es la clave de idempotencia.
    client_uuid: uuid_lib.UUID
    cliente_rut: str
    lineas: list[LineaEntrada] = Field(min_length=1)
    observaciones: str | None = None
    #: Cuándo lo tomó el vendedor, según el reloj del dispositivo. Puede ser muy
    #: anterior al momento de la sincronización.
    creado_en_dispositivo: datetime | None = None


class LineaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sku: str
    descripcion: str
    cantidad: int
    multiplo_venta: int
    precio_unitario_clp: int
    total_linea_clp: int


class EventoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    estado_anterior: str | None
    estado_nuevo: str
    motivo: str | None
    creado_en: datetime


class PedidoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: uuid_lib.UUID
    client_uuid: uuid_lib.UUID
    numero: str
    estado: str
    origen: str
    cliente_rut: str = ""
    neto_clp: int
    iva_clp: int
    total_clp: int
    observaciones: str | None
    creado_en_dispositivo: datetime | None
    sincronizado_en: datetime | None
    lineas: list[LineaOut]
    eventos: list[EventoOut] = Field(default_factory=list)


class PaginaPedidos(BaseModel):
    total: int
    items: list[PedidoOut]


class CambioEstado(BaseModel):
    estado: EstadoPedido
    motivo: str | None = None


# --- endpoints ---------------------------------------------------------------


@router.post("", response_model=PedidoOut)
def crear(
    entrada: PedidoEntrada,
    session: SessionDep,
    response: Response,
    usuario: Annotated[Usuario, Depends(exige_rol("vendedor", "cliente"))],
) -> PedidoOut:
    """Crea el pedido, o devuelve el existente si el `client_uuid` ya se sincronizó."""
    existente = session.scalar(
        select(Pedido)
        .options(
            selectinload(Pedido.lineas),
            selectinload(Pedido.eventos),
            selectinload(Pedido.cliente),
        )
        .where(Pedido.client_uuid == entrada.client_uuid)
    )
    if existente is not None:
        # Reenvío tras perder la señal: misma respuesta, sin duplicar ni fallar.
        return _a_salida(existente)

    cliente = session.scalar(select(Cliente).where(Cliente.rut == entrada.cliente_rut))
    if cliente is None or not cliente.activo:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"No existe el cliente {entrada.cliente_rut}"
        )

    lineas = _resolver_lineas(entrada.lineas, session)
    cfg = get_settings()
    totales = calcular_totales(
        [Linea(cantidad=c, precio_unitario_clp=int(p.precio_lista_clp)) for p, c in lineas],
        iva=Decimal(str(cfg.iva)),
        precios_incluyen_iva=cfg.precios_incluyen_iva,
    )

    ahora = datetime.now(UTC)
    pedido = Pedido(
        client_uuid=entrada.client_uuid,
        numero=siguiente_numero(session, ahora=ahora),
        cliente_id=cliente.id,
        vendedor_id=usuario.id if usuario.rol == "vendedor" else None,
        origen="app_vendedor" if usuario.rol == "vendedor" else "web_cliente",
        estado=EstadoPedido.ENVIADO,
        neto_clp=Decimal(totales.neto_clp),
        iva_clp=Decimal(totales.iva_clp),
        total_clp=Decimal(totales.total_clp),
        observaciones=entrada.observaciones,
        creado_en_dispositivo=entrada.creado_en_dispositivo,
        sincronizado_en=ahora,
    )
    pedido.lineas = [
        PedidoLinea(
            producto_id=producto.id,
            sku=producto.sku,
            descripcion=producto.descripcion,
            multiplo_venta=producto.multiplo_venta,
            cantidad=cantidad,
            precio_unitario_clp=producto.precio_lista_clp,
            total_linea_clp=producto.precio_lista_clp * cantidad,
        )
        for producto, cantidad in lineas
    ]
    pedido.eventos = [
        PedidoEvento(
            estado_anterior=None,
            estado_nuevo=EstadoPedido.ENVIADO,
            usuario_id=usuario.id,
            motivo="Sincronizado desde la app" if usuario.rol == "vendedor" else "Pedido web",
            creado_en=ahora,
        )
    ]

    session.add(pedido)
    session.flush()
    response.status_code = status.HTTP_201_CREATED
    return _a_salida(pedido)


@router.get("", response_model=PaginaPedidos)
def listar(
    session: SessionDep,
    usuario: UsuarioDep,
    estado: Annotated[EstadoPedido | None, Query()] = None,
    cliente_rut: Annotated[str | None, Query()] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginaPedidos:
    consulta = select(Pedido)

    # Un vendedor sólo ve lo suyo. Bodega y admin ven todo.
    if usuario.rol == "vendedor":
        consulta = consulta.where(Pedido.vendedor_id == usuario.id)
    if estado is not None:
        consulta = consulta.where(Pedido.estado == estado)
    if cliente_rut is not None:
        consulta = consulta.join(Cliente).where(Cliente.rut == cliente_rut)

    total = session.scalar(select(func.count()).select_from(consulta.subquery())) or 0
    pedidos = session.scalars(
        consulta.options(selectinload(Pedido.lineas), selectinload(Pedido.cliente))
        .order_by(Pedido.id.desc())
        .limit(limite)
        .offset(offset)
    ).all()

    return PaginaPedidos(total=total, items=[_a_salida(p) for p in pedidos])


@router.get("/{numero}", response_model=PedidoOut)
def obtener(numero: str, session: SessionDep, usuario: UsuarioDep) -> PedidoOut:
    pedido = _buscar(numero, session)
    if usuario.rol == "vendedor" and pedido.vendedor_id != usuario.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="El pedido es de otro vendedor")
    return _a_salida(pedido, con_eventos=True)


@router.post("/{numero}/estado", response_model=PedidoOut)
def cambiar_estado(
    numero: str,
    cambio: CambioEstado,
    session: SessionDep,
    usuario: Annotated[Usuario, Depends(exige_rol("bodega"))],
) -> PedidoOut:
    """Avanza el pedido por su máquina de estados.

    Anular exige motivo: el pedido no se borra nunca, y sin motivo la bitácora no
    sirve para reconstruir qué pasó.
    """
    pedido = _buscar(numero, session)
    actual = EstadoPedido(pedido.estado)

    if cambio.estado == EstadoPedido.ANULADO and not cambio.motivo:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Anular exige un motivo")

    # La mercadería no sale sin guía de despacho electrónica: es la ley, y es lo que le
    # piden al camión en la carretera.
    if cambio.estado == EstadoPedido.DESPACHADO and not tiene_guia(session, pedido.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Falta la guía de despacho electrónica (POST /dte/guias)",
        )

    try:
        nuevo = transicionar(actual, cambio.estado)
    except TransicionInvalida as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    pedido.estado = nuevo
    pedido.eventos.append(
        PedidoEvento(
            estado_anterior=actual,
            estado_nuevo=nuevo,
            usuario_id=usuario.id,
            motivo=cambio.motivo,
            creado_en=datetime.now(UTC),
        )
    )
    session.flush()
    return _a_salida(pedido, con_eventos=True)


# --- apoyo -------------------------------------------------------------------


def _resolver_lineas(entradas: list[LineaEntrada], session: Session) -> list[tuple[Producto, int]]:
    """Valida SKU y múltiplos. Reporta **todos** los problemas de una vez.

    Devolver el primer error obligaría al vendedor a reenviar el pedido tantas veces
    como líneas malas tenga, y cada reenvío necesita señal.
    """
    skus = [e.sku for e in entradas]
    productos = {
        p.sku: p
        for p in session.scalars(
            select(Producto).where(Producto.sku.in_(skus), Producto.activo.is_(True))
        )
    }

    resueltas: list[tuple[Producto, int]] = []
    errores: list[dict[str, object]] = []

    for entrada in entradas:
        producto = productos.get(entrada.sku)
        if producto is None:
            errores.append({"sku": entrada.sku, "error": "producto inexistente o inactivo"})
            continue
        try:
            validar_cantidad(entrada.cantidad, producto.multiplo_venta)
        except CantidadInvalida as exc:
            errores.append(
                {
                    "sku": entrada.sku,
                    "error": str(exc),
                    "multiplo_venta": producto.multiplo_venta,
                    "cantidad_sugerida": ajustar_al_multiplo(
                        entrada.cantidad, producto.multiplo_venta
                    ),
                }
            )
            continue
        resueltas.append((producto, entrada.cantidad))

    if errores:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=errores)
    return resueltas


def _buscar(numero: str, session: Session) -> Pedido:
    pedido = session.scalar(
        select(Pedido)
        .options(
            selectinload(Pedido.lineas),
            selectinload(Pedido.eventos),
            selectinload(Pedido.cliente),
        )
        .where(Pedido.numero == numero)
    )
    if pedido is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe el pedido {numero}")
    return pedido


def _a_salida(pedido: Pedido, *, con_eventos: bool = False) -> PedidoOut:
    salida = PedidoOut.model_validate(pedido)
    salida.cliente_rut = pedido.cliente.rut
    # La bitácora completa sólo va en el detalle: en un listado son cientos de filas
    # que nadie mira.
    if not con_eventos:
        salida.eventos = []
    return salida
