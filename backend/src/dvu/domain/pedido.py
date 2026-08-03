"""Reglas del pedido: máquina de estados y cálculo de totales. Lógica pura."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum


class EstadoPedido(StrEnum):
    BORRADOR = "borrador"
    ENVIADO = "enviado"
    CONFIRMADO = "confirmado"
    PREPARACION = "preparacion"
    DESPACHADO = "despachado"
    ENTREGADO = "entregado"
    CERRADO = "cerrado"
    ANULADO = "anulado"


#: Transiciones permitidas. Una transición inválida es un error explícito: nunca se
#: corrige en silencio, porque enmascara un bug de la app del vendedor.
TRANSICIONES: dict[EstadoPedido, frozenset[EstadoPedido]] = {
    EstadoPedido.BORRADOR: frozenset({EstadoPedido.ENVIADO, EstadoPedido.ANULADO}),
    EstadoPedido.ENVIADO: frozenset({EstadoPedido.CONFIRMADO, EstadoPedido.ANULADO}),
    EstadoPedido.CONFIRMADO: frozenset({EstadoPedido.PREPARACION, EstadoPedido.ANULADO}),
    EstadoPedido.PREPARACION: frozenset({EstadoPedido.DESPACHADO, EstadoPedido.ANULADO}),
    EstadoPedido.DESPACHADO: frozenset({EstadoPedido.ENTREGADO, EstadoPedido.ANULADO}),
    EstadoPedido.ENTREGADO: frozenset({EstadoPedido.CERRADO}),
    EstadoPedido.CERRADO: frozenset(),
    EstadoPedido.ANULADO: frozenset(),
}


class TransicionInvalida(Exception):
    def __init__(self, desde: EstadoPedido, hasta: EstadoPedido) -> None:
        super().__init__(f"No se puede pasar de '{desde}' a '{hasta}'")
        self.desde = desde
        self.hasta = hasta


def puede_transicionar(desde: EstadoPedido, hasta: EstadoPedido) -> bool:
    return hasta in TRANSICIONES[desde]


def transicionar(desde: EstadoPedido, hasta: EstadoPedido) -> EstadoPedido:
    if not puede_transicionar(desde, hasta):
        raise TransicionInvalida(desde, hasta)
    return hasta


# --- cantidades --------------------------------------------------------------


class CantidadInvalida(Exception):
    pass


def validar_cantidad(cantidad: int, multiplo_venta: int) -> None:
    """DVU vende por caja. Pedir 7 unidades de algo que viene de a 12 no es un pedido.

    >>> validar_cantidad(24, 12)
    >>> validar_cantidad(7, 12)
    Traceback (most recent call last):
    ...
    dvu.domain.pedido.CantidadInvalida: La cantidad 7 no es múltiplo de 12
    """
    if cantidad <= 0:
        raise CantidadInvalida(f"La cantidad debe ser positiva, se recibió {cantidad}")
    if multiplo_venta < 1:
        raise CantidadInvalida(f"El múltiplo de venta debe ser >= 1, es {multiplo_venta}")
    if cantidad % multiplo_venta != 0:
        raise CantidadInvalida(f"La cantidad {cantidad} no es múltiplo de {multiplo_venta}")


def ajustar_al_multiplo(cantidad: int, multiplo_venta: int) -> int:
    """Redondea hacia arriba al múltiplo vendible más cercano.

    Se usa como *sugerencia* en la app del vendedor, nunca como corrección
    automática del pedido: el vendedor debe ver y aceptar el cambio.

    >>> ajustar_al_multiplo(7, 12)
    12
    >>> ajustar_al_multiplo(13, 12)
    24
    """
    if multiplo_venta < 1:
        return max(cantidad, 0)
    return max(1, -(-cantidad // multiplo_venta)) * multiplo_venta


# --- totales -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Linea:
    cantidad: int
    precio_unitario_clp: int
    multiplo_venta: int = 1

    @property
    def total_clp(self) -> int:
        return self.cantidad * self.precio_unitario_clp


@dataclass(frozen=True, slots=True)
class Totales:
    neto_clp: int
    iva_clp: int
    total_clp: int


def calcular_totales(
    lineas: list[Linea], *, iva: Decimal = Decimal("0.19"), precios_incluyen_iva: bool = False
) -> Totales:
    """Totaliza un pedido en CLP enteros.

    `precios_incluyen_iva` cubre la pregunta abierta sobre el catálogo (ver
    docs/00-plan-maestro.md §7): si los precios resultan ser brutos, se cambia la
    configuración sin tocar el resto del sistema.

    >>> calcular_totales([Linea(cantidad=12, precio_unitario_clp=1790)])
    Totales(neto_clp=21480, iva_clp=4081, total_clp=25561)
    """
    suma = sum(linea.total_clp for linea in lineas)

    if precios_incluyen_iva:
        neto = _redondear(Decimal(suma) / (Decimal(1) + iva))
        return Totales(neto_clp=neto, iva_clp=suma - neto, total_clp=suma)

    monto_iva = _redondear(Decimal(suma) * iva)
    return Totales(neto_clp=suma, iva_clp=monto_iva, total_clp=suma + monto_iva)


def _redondear(valor: Decimal) -> int:
    """CLP no tiene decimales."""
    return int(valor.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
