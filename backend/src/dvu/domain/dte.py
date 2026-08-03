"""Documentos tributarios electrónicos: reglas de emisión. Lógica pura.

Todo cliente de DVU es empresa y descuenta IVA, así que el documento de venta es la
**factura afecta tipo 33**, nunca boleta. El despacho requiere además **guía tipo 52**.
Corregir una factura ya aceptada por el SII no es editarla: es emitir una **nota de
crédito tipo 61** que la referencia.

Este módulo decide *qué* documento corresponde y *con qué datos*. Quién lo firma y lo
envía al SII es problema de `dvu.integraciones.dte`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class TipoDte(IntEnum):
    FACTURA_AFECTA = 33
    GUIA_DESPACHO = 52
    NOTA_CREDITO = 61


#: Estados del pedido en que ya existe una venta que facturar. Antes de `confirmado`
#: el pedido todavía puede cambiar, y una factura emitida de más obliga a nota de crédito.
ESTADOS_FACTURABLES = frozenset({"confirmado", "preparacion", "despachado", "entregado"})

#: La guía acompaña la mercadería: se emite cuando el pedido ya está armado.
ESTADOS_CON_GUIA = frozenset({"preparacion", "confirmado"})


class NoFacturable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class LineaDte:
    sku: str
    descripcion: str
    cantidad: int
    precio_unitario_clp: int
    total_clp: int


@dataclass(frozen=True, slots=True)
class Emisor:
    rut: str
    razon_social: str
    giro: str
    direccion: str = ""
    comuna: str = ""


@dataclass(frozen=True, slots=True)
class Receptor:
    rut: str
    razon_social: str
    giro: str | None = None
    direccion: str | None = None
    comuna: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentoDte:
    """Lo que se manda a firmar. Montos en CLP enteros, como en todo el sistema."""

    tipo: TipoDte
    emisor: Emisor
    receptor: Receptor
    lineas: tuple[LineaDte, ...]
    neto_clp: int
    iva_clp: int
    total_clp: int
    #: Folio del documento que corrige, sólo en la nota de crédito.
    referencia_folio: int | None = None
    referencia_tipo: TipoDte | None = None
    #: Obligatoria en la nota de crédito: el SII exige decir por qué se anula.
    motivo: str | None = None


def validar_emision(tipo: TipoDte, estado_pedido: str, *, ya_emitido: bool = False) -> None:
    """Reglas de cuándo se puede emitir. Lanza `NoFacturable` con el motivo.

    >>> validar_emision(TipoDte.FACTURA_AFECTA, "confirmado")
    >>> validar_emision(TipoDte.FACTURA_AFECTA, "enviado")
    Traceback (most recent call last):
    ...
    dvu.domain.dte.NoFacturable: No se factura un pedido en estado 'enviado'...
    """
    if estado_pedido == "anulado":
        raise NoFacturable("El pedido está anulado")

    if tipo is TipoDte.FACTURA_AFECTA:
        if ya_emitido:
            raise NoFacturable(
                "El pedido ya tiene factura: para corregirla se emite una nota de crédito"
            )
        if estado_pedido not in ESTADOS_FACTURABLES:
            raise NoFacturable(
                f"No se factura un pedido en estado '{estado_pedido}'; "
                f"se acepta: {', '.join(sorted(ESTADOS_FACTURABLES))}"
            )

    if tipo is TipoDte.GUIA_DESPACHO and estado_pedido not in ESTADOS_CON_GUIA:
        raise NoFacturable(
            f"La guía se emite con el pedido en {', '.join(sorted(ESTADOS_CON_GUIA))}, "
            f"no en '{estado_pedido}'"
        )

    if tipo is TipoDte.NOTA_CREDITO and not ya_emitido:
        raise NoFacturable("No hay factura que anular")


def construir(
    tipo: TipoDte,
    *,
    emisor: Emisor,
    receptor: Receptor,
    lineas: list[LineaDte],
    neto_clp: int,
    iva_clp: int,
    total_clp: int,
    referencia_folio: int | None = None,
    motivo: str | None = None,
) -> DocumentoDte:
    """Arma el documento. Los totales vienen del pedido ya congelado, no se recalculan:
    la factura tiene que decir exactamente lo que se cobró."""
    if not lineas:
        raise NoFacturable("Un documento tributario sin líneas no existe")

    if tipo is TipoDte.NOTA_CREDITO:
        if referencia_folio is None:
            raise NoFacturable("La nota de crédito debe referenciar el folio que corrige")
        if not motivo:
            raise NoFacturable("La nota de crédito exige motivo")

    return DocumentoDte(
        tipo=tipo,
        emisor=emisor,
        receptor=receptor,
        lineas=tuple(lineas),
        neto_clp=neto_clp,
        iva_clp=iva_clp,
        total_clp=total_clp,
        referencia_folio=referencia_folio,
        referencia_tipo=TipoDte.FACTURA_AFECTA if referencia_folio else None,
        motivo=motivo,
    )
