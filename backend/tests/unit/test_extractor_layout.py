"""Tests del agrupamiento posicional.

Las coordenadas provienen de la página 40 de PARTE 1 y de la página 30 de PARTE 2.
Reproducen los dos casos que rompieron el extractor durante su desarrollo:

1. Dos precios consecutivos separados por ~2 pt: fusionarlos borra una fila entera.
2. Una descripción de dos líneas separadas por ~1.4 pt: no fusionarla parte el nombre
   del producto en dos.
"""

from __future__ import annotations

from dvu.extractor.layout import Palabra, agrupar_en_celdas, columna_de, inicio_de_datos


def _p(texto: str, x0: float, top: float, ancho: float = 20, alto: float = 10) -> Palabra:
    return Palabra(texto=texto, x0=x0, x1=x0 + ancho, top=top, bottom=top + alto)


class TestColumnas:
    def test_asignacion_por_x(self) -> None:
        assert columna_de(45) == "codigo"
        assert columna_de(160) == "imagen"
        assert columna_de(270) == "descripcion"
        assert columna_de(380) == "venta_min"
        assert columna_de(450) == "marca"
        assert columna_de(505) == "medida"
        assert columna_de(570) == "precio"

    def test_fuera_de_la_tabla(self) -> None:
        assert columna_de(5) is None
        assert columna_de(700) is None


class TestSignoPeso:
    """El "$" viene como palabra suelta pisando la frontera medida/precio (545.0).

    Medido sobre el PDF real, su centro va de 536.9 a 547.9: el 76% caía del lado de
    `medida`, y por eso "$" es hoy el valor de `medida` más frecuente de la base.
    """

    def test_el_signo_pegado_al_precio_no_ensucia_la_medida(self) -> None:
        celdas = agrupar_en_celdas(
            [
                _p('1/2"', 497, 146.3, ancho=20, alto=9.7),
                _p("$", 534, 146.3, ancho=6, alto=9.7),  # centro 537: caería en medida
                _p("1.790", 552, 146.3, ancho=26, alto=9.7),
            ]
        )
        assert [c.texto for c in celdas["medida"]] == ['1/2"']
        assert [c.texto for c in celdas["precio"]] == ["$ 1.790"]

    def test_el_signo_solo_no_se_confunde_con_una_medida(self) -> None:
        celdas = agrupar_en_celdas([_p("$", 540, 146.3, ancho=6, alto=9.7)])
        assert celdas["medida"] == []
        assert [c.texto for c in celdas["precio"]] == ["$"]


class TestInicioDeDatos:
    def test_detecta_el_encabezado(self) -> None:
        palabras = [_p("Código", 20, 102), _p("Descripción", 253, 102), _p("Venta", 352, 115)]
        assert inicio_de_datos(palabras) > 125

    def test_sin_encabezado_procesa_toda_la_pagina(self) -> None:
        assert inicio_de_datos([_p("CODO", 224, 300)]) == 0.0


class TestFusionDeCeldas:
    def test_precios_consecutivos_no_se_fusionan(self) -> None:
        """455 y 1.058 están a 2.2 pt: son dos productos, no uno."""
        celdas = agrupar_en_celdas(
            [_p("455", 565, 144.8, alto=9.7), _p("1.058", 558, 156.7, alto=9.7)]
        )
        assert [c.texto for c in celdas["precio"]] == ["455", "1.058"]

    def test_medidas_consecutivas_no_se_fusionan(self) -> None:
        celdas = agrupar_en_celdas(
            [_p('1/2"', 497, 146.3, alto=9.7), _p('3/4"', 497, 160.0, alto=9.6)]
        )
        assert [c.texto for c in celdas["medida"]] == ['1/2"', '3/4"']

    def test_descripcion_de_dos_lineas_se_fusiona(self) -> None:
        """«ESPATULA C/ MANGO» + «DOBLE COLOR» son un solo producto."""
        celdas = agrupar_en_celdas(
            [
                _p("ESPATULA", 232, 152.2, ancho=48, alto=11.4),
                _p("C/", 282, 152.2, ancho=9, alto=11.4),
                _p("MANGO", 294, 152.2, ancho=36, alto=11.4),
                _p("DOBLE", 232, 165.0, ancho=32, alto=11.4),
                _p("COLOR", 267, 165.0, ancho=33, alto=11.4),
            ]
        )
        assert [c.texto for c in celdas["descripcion"]] == ["ESPATULA C/ MANGO DOBLE COLOR"]

    def test_venta_min_de_dos_lineas_se_fusiona(self) -> None:
        """«BOLSA» + «X200UN.» es una sola venta mínima."""
        celdas = agrupar_en_celdas(
            [_p("BOLSA", 365, 206.9, ancho=32, alto=11.8), _p("X200UN.", 365, 219.0, alto=11.8)]
        )
        assert [c.texto for c in celdas["venta_min"]] == ["BOLSA X200UN."]

    def test_palabras_de_la_misma_linea_se_ordenan_por_x(self) -> None:
        celdas = agrupar_en_celdas([_p("SO-SO", 255, 154.6), _p("CODO", 224, 154.6)])
        assert celdas["descripcion"][0].texto == "CODO SO-SO"


class TestDistancia:
    def test_cero_dentro_de_la_celda(self) -> None:
        celda = agrupar_en_celdas([_p("CODO", 224, 150, alto=10)])["descripcion"][0]
        assert celda.distancia_a(155) == 0.0

    def test_distancia_al_borde_mas_cercano(self) -> None:
        celda = agrupar_en_celdas([_p("CODO", 224, 150, alto=10)])["descripcion"][0]
        assert celda.distancia_a(170) == 10.0
        assert celda.distancia_a(145) == 5.0
