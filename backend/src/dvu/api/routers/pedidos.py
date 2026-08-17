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

**Borradores.** La lista que el vendedor va armando mientras el ferretero le dicta vive
en el servidor, en estado `borrador`, y no en el navegador: la mañana de un vendedor son
cinco ferreterías, y una lista a medias que se pierde porque se cerró la pestaña o se
apagó el celular es trabajo que hay que volver a hacer con el cliente al lado. Un
borrador todavía **no es un pedido**: no tiene folio, no sale en los listados de ventas
y no existe para el Excel ni para el DTE. Al enviarlo se le asigna el número, se vuelven
a leer los precios del catálogo —el precio que vale es el del momento en que se hace el
pedido, no el de cuando se empezó la lista— y ahí recién se congela todo.
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
    ETIQUETAS,
    CantidadInvalida,
    EstadoPedido,
    Linea,
    Totales,
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
    #: Nulo mientras es borrador: el folio se asigna al enviarlo.
    numero: str | None
    estado: str
    #: El estado en palabras. La pantalla del vendedor no muestra `preparacion`.
    estado_etiqueta: str = ""
    origen: str
    cliente_rut: str = ""
    #: El vendedor reconoce «FERRETERIA EL MARTILLO», no `76123456-0`.
    cliente_razon_social: str = ""
    neto_clp: int
    iva_clp: int
    total_clp: int
    observaciones: str | None
    creado_en_dispositivo: datetime | None
    sincronizado_en: datetime | None
    #: Cuándo se tocó por última vez. Es lo que ordena la lista de borradores.
    actualizado_en: datetime | None = None
    lineas: list[LineaOut]
    eventos: list[EventoOut] = Field(default_factory=list)


class PaginaPedidos(BaseModel):
    total: int
    items: list[PedidoOut]


class CambioEstado(BaseModel):
    estado: EstadoPedido
    motivo: str | None = None


class LineasACotizar(BaseModel):
    """Lo que el vendedor lleva en la lista, para preguntar cuánto sale."""

    lineas: list[LineaEntrada] = Field(min_length=1)


class LineaCotizada(BaseModel):
    sku: str
    descripcion: str = ""
    cantidad: int
    multiplo_venta: int = 1
    #: `cantidad / multiplo_venta`. Es la unidad en que el vendedor piensa y pide.
    envases: int = 0
    precio_unitario_clp: int = 0
    total_linea_clp: int = 0
    #: Qué le pasa a esta línea, en palabras, o `None` si está bien. Una línea con
    #: problema no suma al total: el vendedor tiene que verla, no que se la corrijan.
    problema: str | None = None
    #: La cantidad válida más cercana hacia arriba, para ofrecerla como arreglo.
    cantidad_sugerida: int | None = None


class CotizacionOut(BaseModel):
    neto_clp: int
    iva_clp: int
    total_clp: int
    lineas: list[LineaCotizada]
    #: Cuántas líneas no se pueden pedir tal como están.
    con_problema: int


class BorradorNuevo(BaseModel):
    """Empezar una lista. `client_uuid` lo genera el dispositivo, como el pedido."""

    client_uuid: uuid_lib.UUID
    cliente_rut: str
    lineas: list[LineaEntrada] = Field(default_factory=list)
    observaciones: str | None = None


class BorradorContenido(BaseModel):
    """El contenido completo de la lista, no un cambio sobre lo que había.

    Mandar la lista entera es lo único que no depende del estado anterior, y por lo
    tanto lo único que no se desincroniza si el vendedor tiene la misma lista abierta
    en el celular y en el computador.
    """

    cliente_rut: str
    lineas: list[LineaEntrada] = Field(default_factory=list)
    observaciones: str | None = None


# --- endpoints ---------------------------------------------------------------


@router.post("", response_model=PedidoOut)
def crear(
    entrada: PedidoEntrada,
    session: SessionDep,
    response: Response,
    usuario: Annotated[Usuario, Depends(exige_rol("vendedor", "cliente"))],
) -> PedidoOut:
    """Crea el pedido, o devuelve el existente si el `client_uuid` ya se sincronizó."""
    existente = _por_client_uuid(entrada.client_uuid, session)
    if existente is not None:
        # Reenvío tras perder la señal: misma respuesta, sin duplicar ni fallar.
        return _a_salida(existente)

    cliente = _cliente(entrada.cliente_rut, session)
    lineas = _resolver_lineas(entrada.lineas, session)

    ahora = datetime.now(UTC)
    pedido = Pedido(
        client_uuid=entrada.client_uuid,
        numero=siguiente_numero(session, ahora=ahora),
        cliente_id=cliente.id,
        vendedor_id=usuario.id if usuario.rol == "vendedor" else None,
        origen="app_vendedor" if usuario.rol == "vendedor" else "web_cliente",
        estado=EstadoPedido.ENVIADO,
        observaciones=entrada.observaciones,
        creado_en_dispositivo=entrada.creado_en_dispositivo,
        sincronizado_en=ahora,
    )
    _congelar(pedido, lineas)
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
    else:
        # Una lista a medias no es un pedido: no tiene folio y nadie la pidió todavía.
        # Vive en `/pedidos/borradores` y no ensucia «mis últimos pedidos» ni la
        # bandeja de bodega. Se puede pedir explícita con `?estado=borrador`.
        consulta = consulta.where(Pedido.estado != EstadoPedido.BORRADOR)
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


# --- cotizar y borradores ----------------------------------------------------
#
# Estas rutas van **antes** de `/{numero}`: FastAPI resuelve por orden de declaración y
# `/pedidos/cotizar` entraría por la ruta del folio.

#: Las listas guardadas son del vendedor que las arma. Un usuario con rol `cliente` no
#: tiene a quién atribuírselas —`vendedor_id` queda nulo y no hay vínculo usuario↔cliente—
#: así que sigue armando su pedido en el navegador y enviándolo con `POST /pedidos`.
VendedorDep = Annotated[Usuario, Depends(exige_rol("vendedor"))]


@router.post("/cotizar", response_model=CotizacionOut)
def cotizar(entrada: LineasACotizar, session: SessionDep, usuario: UsuarioDep) -> CotizacionOut:
    """Cuánto sale el pedido —con IVA— sin crear nada.

    Existe porque el ferretero pregunta «¿en cuánto me queda?» antes de cerrar el pedido
    y la página no puede responderlo sola: la regla del impuesto vive en el dominio y no
    se repite en JavaScript. De paso el vendedor ve el precio de hoy y descubre una
    cantidad que dejó de ser múltiplo mientras arma la lista, no al enviarla.

    A diferencia de crear, esto **no falla** por una línea mala: la marca y sigue. Es una
    pantalla que se refresca mientras se escribe, no un compromiso.
    """
    return _cotizar(entrada.lineas, session)


@router.get("/borradores", response_model=list[PedidoOut])
def listar_borradores(session: SessionDep, usuario: VendedorDep) -> list[PedidoOut]:
    """Las listas que este vendedor tiene a medias, la más reciente primero."""
    borradores = session.scalars(
        select(Pedido)
        .options(selectinload(Pedido.lineas), selectinload(Pedido.cliente))
        .where(Pedido.estado == EstadoPedido.BORRADOR, Pedido.vendedor_id == usuario.id)
        .order_by(Pedido.actualizado_en.desc())
    ).all()
    return [_a_salida(b) for b in borradores]


@router.post("/borradores", response_model=PedidoOut)
def crear_borrador(
    entrada: BorradorNuevo, session: SessionDep, response: Response, usuario: VendedorDep
) -> PedidoOut:
    """Empieza una lista para un cliente. Idempotente por `client_uuid`, como el pedido."""
    existente = _por_client_uuid(entrada.client_uuid, session)
    if existente is not None:
        return _a_salida(existente)

    ahora = datetime.now(UTC)
    pedido = Pedido(
        client_uuid=entrada.client_uuid,
        numero=None,
        cliente_id=_cliente(entrada.cliente_rut, session).id,
        vendedor_id=usuario.id,
        origen="app_vendedor",
        estado=EstadoPedido.BORRADOR,
        observaciones=entrada.observaciones,
        # Cuándo empezó la visita, según el reloj del vendedor. Al enviar no se pisa:
        # es el dato que dice cuándo se tomó el pedido de verdad.
        creado_en_dispositivo=ahora,
    )
    pedido.eventos = [
        PedidoEvento(
            estado_anterior=None,
            estado_nuevo=EstadoPedido.BORRADOR,
            usuario_id=usuario.id,
            motivo="Lista empezada por el vendedor",
            creado_en=ahora,
        )
    ]
    session.add(pedido)
    _poner_contenido(pedido, entrada.lineas, session)
    session.flush()
    response.status_code = status.HTTP_201_CREATED
    return _a_salida(pedido)


@router.get("/borradores/{client_uuid}", response_model=PedidoOut)
def obtener_borrador(
    client_uuid: uuid_lib.UUID, session: SessionDep, usuario: VendedorDep
) -> PedidoOut:
    return _a_salida(_buscar_borrador(client_uuid, session, usuario))


@router.put("/borradores/{client_uuid}", response_model=PedidoOut)
def guardar_borrador(
    client_uuid: uuid_lib.UUID,
    contenido: BorradorContenido,
    session: SessionDep,
    usuario: VendedorDep,
) -> PedidoOut:
    """Reescribe la lista completa.

    El borrador es permisivo a propósito: acepta una cantidad que no es múltiplo del
    envase y la deja marcada. Rechazar el guardado obligaría al vendedor a arreglarla
    con el cliente esperando, o a perderla. La estrictez está en enviar, que es donde
    el pedido se vuelve un compromiso.
    """
    borrador = _buscar_borrador(client_uuid, session, usuario)
    borrador.cliente_id = _cliente(contenido.cliente_rut, session).id
    borrador.observaciones = contenido.observaciones
    _poner_contenido(borrador, contenido.lineas, session)
    session.flush()
    return _a_salida(borrador)


@router.post("/borradores/{client_uuid}/enviar", response_model=PedidoOut)
def enviar_borrador(
    client_uuid: uuid_lib.UUID, session: SessionDep, usuario: VendedorDep
) -> PedidoOut:
    """Convierte la lista en pedido: le da folio, congela precios y la manda a DVU.

    Los precios se vuelven a leer del catálogo. El que vale es el del momento en que se
    hace el pedido, no el de cuando se empezó la lista, que pueden ser días distintos.
    """
    borrador = _buscar_borrador(client_uuid, session, usuario)
    if not borrador.lineas:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Una lista vacía no es un pedido"
        )

    lineas = _resolver_lineas(
        [LineaEntrada(sku=linea.sku, cantidad=linea.cantidad) for linea in borrador.lineas],
        session,
    )
    _congelar(borrador, lineas)

    ahora = datetime.now(UTC)
    borrador.numero = siguiente_numero(session, ahora=ahora)
    borrador.estado = transicionar(EstadoPedido.BORRADOR, EstadoPedido.ENVIADO)
    borrador.sincronizado_en = ahora
    borrador.eventos.append(
        PedidoEvento(
            estado_anterior=EstadoPedido.BORRADOR,
            estado_nuevo=EstadoPedido.ENVIADO,
            usuario_id=usuario.id,
            motivo="Enviado por el vendedor",
            creado_en=ahora,
        )
    )
    session.flush()
    return _a_salida(borrador)


@router.delete("/borradores/{client_uuid}", response_model=PedidoOut)
def descartar_borrador(
    client_uuid: uuid_lib.UUID, session: SessionDep, usuario: VendedorDep
) -> PedidoOut:
    """Descarta la lista. No se borra: queda anulada, como todo acá."""
    borrador = _buscar_borrador(client_uuid, session, usuario)
    borrador.estado = transicionar(EstadoPedido.BORRADOR, EstadoPedido.ANULADO)
    borrador.eventos.append(
        PedidoEvento(
            estado_anterior=EstadoPedido.BORRADOR,
            estado_nuevo=EstadoPedido.ANULADO,
            usuario_id=usuario.id,
            motivo="Lista descartada por el vendedor",
            creado_en=datetime.now(UTC),
        )
    )
    session.flush()
    return _a_salida(borrador)


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


def _productos(skus: list[str], session: Session) -> dict[str, Producto]:
    return {
        p.sku: p
        for p in session.scalars(
            select(Producto).where(Producto.sku.in_(skus), Producto.activo.is_(True))
        )
    }


def _resolver_lineas(entradas: list[LineaEntrada], session: Session) -> list[tuple[Producto, int]]:
    """Valida SKU y múltiplos. Reporta **todos** los problemas de una vez.

    Devolver el primer error obligaría al vendedor a reenviar el pedido tantas veces
    como líneas malas tenga, y cada reenvío necesita señal.
    """
    productos = _productos([e.sku for e in entradas], session)

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


def _evaluar(
    entradas: list[LineaEntrada], session: Session
) -> tuple[list[tuple[LineaCotizada, Producto | None]], Totales]:
    """Precia cada línea contra el catálogo de hoy y marca las que no se pueden pedir.

    Es la versión que no falla de `_resolver_lineas`, y la usan las dos pantallas que
    muestran plata antes de comprometer nada: la cotización y el borrador.
    """
    productos = _productos([e.sku for e in entradas], session)
    evaluadas: list[tuple[LineaCotizada, Producto | None]] = []
    vendibles: list[Linea] = []

    for entrada in entradas:
        producto = productos.get(entrada.sku)
        if producto is None:
            evaluadas.append(
                (
                    LineaCotizada(
                        sku=entrada.sku,
                        cantidad=entrada.cantidad,
                        problema="Ya no está en el catálogo",
                    ),
                    None,
                )
            )
            continue

        precio = int(producto.precio_lista_clp)
        cotizada = LineaCotizada(
            sku=producto.sku,
            descripcion=producto.descripcion,
            cantidad=entrada.cantidad,
            multiplo_venta=producto.multiplo_venta,
            envases=entrada.cantidad // producto.multiplo_venta,
            precio_unitario_clp=precio,
            total_linea_clp=precio * entrada.cantidad,
        )
        try:
            validar_cantidad(entrada.cantidad, producto.multiplo_venta)
        except CantidadInvalida:
            # Pasa al repetir un pedido viejo: el envase cambió de 12 a 24 y la cantidad
            # de entonces dejó de ser vendible. Se marca y no suma; el vendedor decide.
            cotizada.problema = (
                f"Se vende de a {producto.multiplo_venta}: {entrada.cantidad} no calza"
            )
            cotizada.cantidad_sugerida = ajustar_al_multiplo(
                entrada.cantidad, producto.multiplo_venta
            )
            cotizada.total_linea_clp = 0
        else:
            vendibles.append(Linea(cantidad=entrada.cantidad, precio_unitario_clp=precio))
        evaluadas.append((cotizada, producto))

    return evaluadas, _totales(vendibles)


def _totales(lineas: list[Linea]) -> Totales:
    cfg = get_settings()
    return calcular_totales(
        lineas, iva=Decimal(str(cfg.iva)), precios_incluyen_iva=cfg.precios_incluyen_iva
    )


def _cotizar(entradas: list[LineaEntrada], session: Session) -> CotizacionOut:
    evaluadas, totales = _evaluar(entradas, session)
    lineas = [cotizada for cotizada, _ in evaluadas]
    return CotizacionOut(
        neto_clp=totales.neto_clp,
        iva_clp=totales.iva_clp,
        total_clp=totales.total_clp,
        lineas=lineas,
        con_problema=sum(1 for linea in lineas if linea.problema),
    )


def _congelar(pedido: Pedido, lineas: list[tuple[Producto, int]]) -> None:
    """Deja las líneas con el precio y la descripción de este momento, y totaliza.

    Congelar es lo que hace que un pedido de hace seis meses siga siendo legible y
    auditable aunque el catálogo haya cambiado tres veces desde entonces.
    """
    totales = _totales(
        [Linea(cantidad=c, precio_unitario_clp=int(p.precio_lista_clp)) for p, c in lineas]
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
    pedido.neto_clp = Decimal(totales.neto_clp)
    pedido.iva_clp = Decimal(totales.iva_clp)
    pedido.total_clp = Decimal(totales.total_clp)


def _poner_contenido(pedido: Pedido, entradas: list[LineaEntrada], session: Session) -> None:
    """Reemplaza las líneas del borrador por las que llegaron.

    Acepta cantidades que no son múltiplo —quedan marcadas al cotizar— pero no SKU que
    no existen: la página sólo puede mandar los que sacó del catálogo, así que uno
    desconocido es un error de programa, no del vendedor.
    """
    evaluadas, totales = _evaluar(entradas, session)
    desconocidos = [cotizada.sku for cotizada, producto in evaluadas if producto is None]
    if desconocidos:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"No están en el catálogo: {', '.join(desconocidos)}",
        )

    pedido.lineas = [
        PedidoLinea(
            producto_id=producto.id,
            sku=producto.sku,
            descripcion=producto.descripcion,
            multiplo_venta=producto.multiplo_venta,
            cantidad=cotizada.cantidad,
            precio_unitario_clp=producto.precio_lista_clp,
            total_linea_clp=producto.precio_lista_clp * cotizada.cantidad,
        )
        for cotizada, producto in evaluadas
        if producto is not None
    ]
    pedido.neto_clp = Decimal(totales.neto_clp)
    pedido.iva_clp = Decimal(totales.iva_clp)
    pedido.total_clp = Decimal(totales.total_clp)


def _cliente(rut: str, session: Session) -> Cliente:
    cliente = session.scalar(select(Cliente).where(Cliente.rut == rut))
    if cliente is None or not cliente.activo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe el cliente {rut}")
    return cliente


def _por_client_uuid(client_uuid: uuid_lib.UUID, session: Session) -> Pedido | None:
    return session.scalar(
        select(Pedido)
        .options(
            selectinload(Pedido.lineas),
            selectinload(Pedido.eventos),
            selectinload(Pedido.cliente),
        )
        .where(Pedido.client_uuid == client_uuid)
    )


def _buscar_borrador(client_uuid: uuid_lib.UUID, session: Session, usuario: Usuario) -> Pedido:
    pedido = _por_client_uuid(client_uuid, session)
    # Una lista de otro vendedor se responde igual que una que no existe: quién más
    # tiene listas abiertas no es asunto de nadie.
    if pedido is None or pedido.vendedor_id != usuario.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe la lista {client_uuid}")
    if pedido.estado != EstadoPedido.BORRADOR:
        detalle = (
            f"Esa lista ya se envió: es el pedido {pedido.numero}"
            if pedido.estado == EstadoPedido.ENVIADO
            else f"Esa lista ya no está abierta ({ETIQUETAS[EstadoPedido(pedido.estado)]})"
        )
        raise HTTPException(status.HTTP_409_CONFLICT, detail=detalle)
    return pedido


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
    salida.cliente_razon_social = pedido.cliente.razon_social
    salida.estado_etiqueta = ETIQUETAS.get(EstadoPedido(pedido.estado), pedido.estado)
    # La bitácora completa sólo va en el detalle: en un listado son cientos de filas
    # que nadie mira.
    if not con_eventos:
        salida.eventos = []
    return salida
