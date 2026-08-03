"""Los casos de estos tests son literales del catálogo de julio 2026."""

from __future__ import annotations

from decimal import Decimal

import pytest

from dvu.domain.catalogo import (
    FilaNormalizada,
    diagnosticar,
    generar_sku,
    normalizar_codigo,
    parse_medida,
    parse_precio_clp,
    parse_venta_minima,
)


class TestPrecio:
    @pytest.mark.parametrize(
        ("crudo", "esperado"),
        [
            ("1.790", 1790),
            ("62.500", 62500),
            ("455", 455),
            ("314.764", 314764),
            ("$ 2.450", 2450),
            (" 990 ", 990),
        ],
    )
    def test_formatos_reales(self, crudo: str, esperado: int) -> None:
        assert parse_precio_clp(crudo) == esperado

    @pytest.mark.parametrize("crudo", ["", None, "s/p", "1.79", "abc", "0"])
    def test_no_parseables_son_none(self, crudo: str | None) -> None:
        assert parse_precio_clp(crudo) is None

    def test_precio_cero_es_dato_faltante(self) -> None:
        # Un producto a $0 en el catálogo es un dato que falta, no una promoción.
        assert parse_precio_clp("0") is None


class TestVentaMinima:
    @pytest.mark.parametrize(
        ("crudo", "multiplo", "unidad", "envase"),
        [
            ("X 12 UNID", 12, "UNID", None),
            ("X20 UN", 20, "UNID", None),
            ("X 4 UN", 4, "UNID", None),
            ("BOLSA X200UN.", 200, "BOLSA", "BOLSA"),
            ("X 8 UN", 8, "UNID", None),
            ("X 50 MTS", 50, "MT", None),
        ],
    )
    def test_formatos_reales(
        self, crudo: str, multiplo: int, unidad: str, envase: str | None
    ) -> None:
        vm = parse_venta_minima(crudo)
        assert vm.multiplo == multiplo
        assert vm.envase == envase
        assert vm.confianza == 1.0

    def test_x_un_sin_numero_es_unitario(self) -> None:
        vm = parse_venta_minima("X UN")
        assert vm.multiplo == 1
        assert vm.confianza < 1.0

    def test_ausente_no_inventa_multiplo(self) -> None:
        """Sin dato, el sistema queda operativo pero la fila se marca para revisión."""
        vm = parse_venta_minima(None)
        assert vm.multiplo == 1
        assert vm.confianza == 0.0


class TestMedida:
    def test_fraccion_de_pulgada(self) -> None:
        m = parse_medida('1/2"')
        assert m.unidad == "PULG"
        assert m.valor == Decimal("0.5")

    def test_fraccion_mixta(self) -> None:
        assert parse_medida('2 1/2"').valor == Decimal("2.5")

    def test_milimetros(self) -> None:
        m = parse_medida("3 MM")
        assert (m.valor, m.unidad) == (Decimal("3"), "MM")

    @pytest.mark.parametrize("crudo", ["75W/80", "350X8", '3/4" X 1/2'])
    def test_no_dimensionales_se_conservan_como_texto(self, crudo: str) -> None:
        m = parse_medida(crudo)
        assert m.valor is None
        assert m.texto == crudo


class TestCodigos:
    @pytest.mark.parametrize(
        ("crudo", "esperado"),
        [
            ("  pr/49573 ", "PR/49573"),
            ("080633000-T", "080633000-T"),
            ("FERCADGAL  174", "FERCADGAL 174"),
            ("ASK11003", "ASK11003"),
        ],
    )
    def test_normaliza_las_cinco_familias(self, crudo: str, esperado: str) -> None:
        assert normalizar_codigo(crudo) == esperado

    @pytest.mark.parametrize("crudo", ["", None, "—", "-"])
    def test_basura_es_none(self, crudo: str | None) -> None:
        assert normalizar_codigo(crudo) is None

    def test_sku_es_determinista(self) -> None:
        """Re-extraer el catálogo no debe generar SKUs nuevos para los mismos productos."""
        assert generar_sku("PR/49573") == generar_sku("PR/49573") == "DVU-PR49573"

    def test_sku_limpia_separadores(self) -> None:
        assert generar_sku("080633000-T") == "DVU-080633000T"


class TestDiagnostico:
    def _fila(self, **kwargs: object) -> FilaNormalizada:
        base: dict[str, object] = {
            "codigo": "PR/49573",
            "sku": "DVU-PR49573",
            "descripcion": "LIQUIDO DE FRENO FEDERAL",
            "venta_minima": parse_venta_minima("X 12 UNID"),
            "marca": "FEDERAL",
            "medida": parse_medida('1/2"'),
            "precio_clp": 1790,
            "pagina": 3,
            "orden": 0,
        }
        base.update(kwargs)
        return FilaNormalizada(**base)  # type: ignore[arg-type]

    def test_fila_completa_no_tiene_problemas(self) -> None:
        fila = self._fila()
        assert diagnosticar(fila) == []
        assert fila.cargable
        assert fila.confianza == 1.0

    def test_sin_precio_no_es_cargable(self) -> None:
        fila = self._fila(precio_clp=None)
        assert not fila.cargable
        assert "sin_precio" in diagnosticar(fila)

    def test_sin_codigo_no_es_cargable(self) -> None:
        assert not self._fila(codigo=None).cargable

    def test_precio_absurdo_se_marca(self) -> None:
        assert "precio_sospechoso_alto" in diagnosticar(self._fila(precio_clp=9_000_000))
