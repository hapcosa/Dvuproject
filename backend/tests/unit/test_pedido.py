from __future__ import annotations

from decimal import Decimal

import pytest

from dvu.domain.pedido import (
    TRANSICIONES,
    CantidadInvalida,
    EstadoPedido,
    Linea,
    TransicionInvalida,
    ajustar_al_multiplo,
    calcular_totales,
    puede_transicionar,
    transicionar,
    validar_cantidad,
)


class TestMaquinaDeEstados:
    def test_camino_feliz_completo(self) -> None:
        estado = EstadoPedido.BORRADOR
        for siguiente in (
            EstadoPedido.ENVIADO,
            EstadoPedido.CONFIRMADO,
            EstadoPedido.PREPARACION,
            EstadoPedido.DESPACHADO,
            EstadoPedido.ENTREGADO,
            EstadoPedido.CERRADO,
        ):
            estado = transicionar(estado, siguiente)
        assert estado is EstadoPedido.CERRADO

    def test_no_se_puede_saltar_etapas(self) -> None:
        with pytest.raises(TransicionInvalida):
            transicionar(EstadoPedido.ENVIADO, EstadoPedido.DESPACHADO)

    def test_anulado_es_terminal(self) -> None:
        assert not puede_transicionar(EstadoPedido.ANULADO, EstadoPedido.ENVIADO)

    def test_cerrado_es_terminal(self) -> None:
        assert TRANSICIONES[EstadoPedido.CERRADO] == frozenset()

    def test_se_puede_anular_hasta_el_despacho(self) -> None:
        for estado in (
            EstadoPedido.ENVIADO,
            EstadoPedido.CONFIRMADO,
            EstadoPedido.PREPARACION,
            EstadoPedido.DESPACHADO,
        ):
            assert puede_transicionar(estado, EstadoPedido.ANULADO)

    def test_un_pedido_entregado_ya_no_se_anula(self) -> None:
        assert not puede_transicionar(EstadoPedido.ENTREGADO, EstadoPedido.ANULADO)


class TestVentaPorMultiplos:
    """La regla que diferencia a DVU de un ecommerce B2C: se vende por caja."""

    def test_multiplo_exacto_es_valido(self) -> None:
        validar_cantidad(24, 12)

    def test_cantidad_no_multiplo_se_rechaza(self) -> None:
        with pytest.raises(CantidadInvalida, match="no es múltiplo de 12"):
            validar_cantidad(7, 12)

    def test_cantidad_cero_o_negativa_se_rechaza(self) -> None:
        with pytest.raises(CantidadInvalida):
            validar_cantidad(0, 12)
        with pytest.raises(CantidadInvalida):
            validar_cantidad(-12, 12)

    def test_producto_unitario_acepta_cualquier_cantidad(self) -> None:
        validar_cantidad(7, 1)

    @pytest.mark.parametrize(
        ("cantidad", "multiplo", "esperado"),
        [(7, 12, 12), (13, 12, 24), (12, 12, 12), (1, 200, 200), (201, 200, 400)],
    )
    def test_sugerencia_redondea_hacia_arriba(
        self, cantidad: int, multiplo: int, esperado: int
    ) -> None:
        assert ajustar_al_multiplo(cantidad, multiplo) == esperado


class TestTotales:
    def test_precios_netos(self) -> None:
        totales = calcular_totales([Linea(cantidad=12, precio_unitario_clp=1790)])
        assert totales.neto_clp == 21480
        assert totales.iva_clp == 4081
        assert totales.total_clp == 25561

    def test_precios_con_iva_incluido(self) -> None:
        """Cubre la pregunta abierta sobre el catálogo: si los precios son brutos,
        cambia una variable de entorno y nada más."""
        totales = calcular_totales(
            [Linea(cantidad=1, precio_unitario_clp=11900)], precios_incluyen_iva=True
        )
        assert totales.total_clp == 11900
        assert totales.neto_clp == 10000
        assert totales.iva_clp == 1900

    def test_todo_es_entero_clp(self) -> None:
        totales = calcular_totales(
            [Linea(cantidad=3, precio_unitario_clp=333), Linea(cantidad=7, precio_unitario_clp=101)]
        )
        assert isinstance(totales.neto_clp, int)
        assert isinstance(totales.iva_clp, int)
        assert isinstance(totales.total_clp, int)

    def test_pedido_vacio(self) -> None:
        assert calcular_totales([]).total_clp == 0

    def test_iva_configurable(self) -> None:
        totales = calcular_totales(
            [Linea(cantidad=1, precio_unitario_clp=1000)], iva=Decimal("0.10")
        )
        assert totales.iva_clp == 100
