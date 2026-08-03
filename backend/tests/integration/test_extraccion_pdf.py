"""Test de extremo a extremo contra el catálogo real.

Marcado `pdf`: se salta si los PDF no están presentes (no se versionan, pesan ~380 MB).
Es la única verificación de que el extractor sigue cumpliendo el criterio de salida
de la Fase 0 cuando cambian las heurísticas.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dvu.extractor.catalogo_pdf import extraer_pdf
from dvu.extractor.reporte import construir_reporte

CATALOGO = Path(__file__).resolve().parents[3] / "catalago"
PARTE_1 = CATALOGO / "CAT ACT 10 JULIO 2026 PARTE 1.pdf"

pytestmark = [
    pytest.mark.pdf,
    pytest.mark.skipif(not PARTE_1.exists(), reason="catálogo PDF no disponible"),
]


@pytest.fixture(scope="module")
def pagina_40() -> list:
    return extraer_pdf(PARTE_1, desde_pagina=40, hasta_pagina=40).filas


def test_extrae_todas_las_filas_de_la_pagina(pagina_40: list) -> None:
    """La página 40 (fittings de bronce) tiene 20 productos en el PDF."""
    assert len(pagina_40) == 20


def test_variantes_de_una_familia_comparten_descripcion(pagina_40: list) -> None:
    codos = [f for f in pagina_40 if f.descripcion == "CODO SO-SO"]
    assert len(codos) == 3
    assert {f.codigo for f in codos} == {"080633000-T", "080644000-T", "080655000-T"}


def test_cada_variante_tiene_su_propia_medida_y_precio(pagina_40: list) -> None:
    """Sin el reparto exclusivo, las variantes copian la medida de la fila vecina."""
    tee = sorted(
        (f for f in pagina_40 if f.descripcion == "TEE SO-SO-SO"), key=lambda f: f.precio_clp
    )
    assert [(f.medida.texto, f.precio_clp) for f in tee] == [('1/2"', 690), ('3/4"', 1803)]


def test_venta_minima_propia_por_variante(pagina_40: list) -> None:
    codo_hi = sorted(
        (f for f in pagina_40 if f.descripcion == "CODO SO-HE"), key=lambda f: f.precio_clp
    )
    assert [f.venta_minima.multiplo for f in codo_hi] == [20, 20]


def test_precios_son_enteros_clp(pagina_40: list) -> None:
    for fila in pagina_40:
        assert isinstance(fila.precio_clp, int)
        assert 0 < fila.precio_clp < 1_000_000


@pytest.mark.slow
def test_criterio_de_salida_fase_0() -> None:
    """≥95% de las filas deben ser cargables. Es el criterio de la Fase 0."""
    resultado = extraer_pdf(PARTE_1)
    reporte = construir_reporte([resultado])
    assert reporte.porcentaje_cargable >= 95, reporte.resumen()
