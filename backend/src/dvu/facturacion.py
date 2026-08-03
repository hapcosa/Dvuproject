"""Orquestación de la emisión de DTE.

Traduce un `Pedido` de la base al documento que espera el SII, lo manda a emitir por el
proveedor y guarda el resultado en `dte`.

La regla que sostiene todo: **un pedido tiene a lo más una factura viva**. Si la factura
tiene un error, no se edita ni se borra: se emite una nota de crédito que la referencia
y recién entonces se puede volver a facturar.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from dvu.config import get_settings
from dvu.db.models import Dte, Pedido
from dvu.domain.dte import (
    DocumentoDte,
    Emisor,
    LineaDte,
    NoFacturable,
    Receptor,
    TipoDte,
    construir,
    validar_emision,
)
from dvu.integraciones.dte import ProveedorDte, get_dte

#: Estados del DTE en que la factura sigue vigente. Una `anulada` o `rechazada` no
#: bloquea volver a facturar el pedido.
ESTADOS_VIGENTES = ("emitido", "aceptado")


def emitir_factura(
    session: Session,
    *,
    pedido_id: int,
    usuario_id: int | None = None,
    proveedor: ProveedorDte | None = None,
) -> Dte:
    """Factura afecta tipo 33. El único documento de venta válido para DVU: sus clientes
    son empresas y descuentan IVA, así que la boleta no sirve."""
    pedido = _cargar(session, pedido_id)
    validar_emision(
        TipoDte.FACTURA_AFECTA,
        pedido.estado,
        ya_emitido=_vigente(session, pedido_id, TipoDte.FACTURA_AFECTA) is not None,
    )

    documento = construir(
        TipoDte.FACTURA_AFECTA,
        emisor=_emisor(),
        receptor=_receptor(pedido),
        lineas=_lineas(pedido),
        neto_clp=int(pedido.neto_clp),
        iva_clp=int(pedido.iva_clp),
        total_clp=int(pedido.total_clp),
    )
    return _emitir(session, pedido, documento, usuario_id, proveedor)


def emitir_guia(
    session: Session,
    *,
    pedido_id: int,
    usuario_id: int | None = None,
    proveedor: ProveedorDte | None = None,
) -> Dte:
    """Guía de despacho tipo 52. Acompaña la mercadería: sin ella el camión no puede
    salir con el pedido."""
    pedido = _cargar(session, pedido_id)
    if _vigente(session, pedido_id, TipoDte.GUIA_DESPACHO) is not None:
        raise NoFacturable("El pedido ya tiene guía de despacho")
    validar_emision(TipoDte.GUIA_DESPACHO, pedido.estado)

    documento = construir(
        TipoDte.GUIA_DESPACHO,
        emisor=_emisor(),
        receptor=_receptor(pedido),
        lineas=_lineas(pedido),
        neto_clp=int(pedido.neto_clp),
        iva_clp=int(pedido.iva_clp),
        total_clp=int(pedido.total_clp),
    )
    return _emitir(session, pedido, documento, usuario_id, proveedor)


def emitir_nota_credito(
    session: Session,
    *,
    pedido_id: int,
    motivo: str,
    usuario_id: int | None = None,
    proveedor: ProveedorDte | None = None,
) -> Dte:
    """Nota de crédito tipo 61: la única forma de deshacer una factura ya emitida."""
    pedido = _cargar(session, pedido_id)
    factura = _vigente(session, pedido_id, TipoDte.FACTURA_AFECTA)
    validar_emision(TipoDte.NOTA_CREDITO, pedido.estado, ya_emitido=factura is not None)
    if factura is None:  # `validar_emision` ya lo cubre; esto es para el tipo
        raise NoFacturable("No hay factura que anular")

    documento = construir(
        TipoDte.NOTA_CREDITO,
        emisor=_emisor(),
        receptor=_receptor(pedido),
        lineas=_lineas(pedido),
        neto_clp=int(factura.neto_clp),
        iva_clp=int(factura.iva_clp),
        total_clp=int(factura.total_clp),
        referencia_folio=factura.folio,
        motivo=motivo,
    )
    nota = _emitir(session, pedido, documento, usuario_id, proveedor)
    nota.referencia_dte_id = factura.id
    # La factura queda anulada, pero la fila no se borra: el SII exige conservar el
    # rastro de los dos documentos.
    factura.estado = "anulado"
    session.flush()
    return nota


def tiene_guia(session: Session, pedido_id: int) -> bool:
    """Un pedido no pasa a `despachado` sin guía electrónica emitida."""
    return _vigente(session, pedido_id, TipoDte.GUIA_DESPACHO) is not None


# --- apoyo -------------------------------------------------------------------


def _emitir(
    session: Session,
    pedido: Pedido,
    documento: DocumentoDte,
    usuario_id: int | None,
    proveedor: ProveedorDte | None,
) -> Dte:
    emisor = proveedor or get_dte(_folio_desde_bd(session))
    emision = emisor.emitir(documento)

    dte = Dte(
        tipo=documento.tipo.value,
        folio=emision.folio,
        pedido_id=pedido.id,
        cliente_id=pedido.cliente_id,
        rut_receptor=documento.receptor.rut,
        neto_clp=Decimal(documento.neto_clp),
        iva_clp=Decimal(documento.iva_clp),
        total_clp=Decimal(documento.total_clp),
        estado=emision.estado,
        track_id=emision.track_id,
        glosa_sii=emision.glosa,
        emitido_por=usuario_id,
    )
    session.add(dte)
    session.flush()
    return dte


def _folio_desde_bd(session: Session) -> Callable[[TipoDte], int]:
    """Contador de folios para el proveedor `fake`.

    Sale de la base y no de memoria porque `(tipo, folio)` es único: dos corridas del
    stack de desarrollo repitiendo folios chocarían contra la restricción.
    """

    def siguiente(tipo: TipoDte) -> int:
        maximo = session.scalar(select(func.max(Dte.folio)).where(Dte.tipo == tipo.value))
        return int(maximo or 0) + 1

    return siguiente


def _cargar(session: Session, pedido_id: int) -> Pedido:
    pedido = session.scalar(
        select(Pedido)
        .options(selectinload(Pedido.lineas), selectinload(Pedido.cliente))
        .where(Pedido.id == pedido_id)
    )
    if pedido is None:
        raise LookupError(f"No existe el pedido {pedido_id}")
    return pedido


def _vigente(session: Session, pedido_id: int, tipo: TipoDte) -> Dte | None:
    return session.scalar(
        select(Dte)
        .where(
            Dte.pedido_id == pedido_id,
            Dte.tipo == tipo.value,
            Dte.estado.in_(ESTADOS_VIGENTES),
        )
        .order_by(Dte.id.desc())
    )


def _emisor() -> Emisor:
    cfg = get_settings()
    return Emisor(
        rut=cfg.emisor_rut,
        razon_social=cfg.emisor_razon_social,
        giro=cfg.emisor_giro,
        direccion=cfg.emisor_direccion,
        comuna=cfg.emisor_comuna,
    )


def _receptor(pedido: Pedido) -> Receptor:
    cliente = pedido.cliente
    return Receptor(
        rut=cliente.rut,
        razon_social=cliente.razon_social,
        giro=cliente.giro,
        direccion=cliente.direccion,
        comuna=cliente.comuna,
    )


def _lineas(pedido: Pedido) -> list[LineaDte]:
    return [
        LineaDte(
            sku=linea.sku,
            descripcion=linea.descripcion,
            cantidad=linea.cantidad,
            precio_unitario_clp=int(linea.precio_unitario_clp),
            total_clp=int(linea.total_linea_clp),
        )
        for linea in pedido.lineas
    ]
