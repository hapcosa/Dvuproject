"""Reglas de clasificación del catálogo.

Los casos de acá salen de descripciones reales del PDF de DVU, no de ejemplos
inventados: lo que rompe un clasificador de palabras clave son las ambigüedades del
vocabulario del negocio, y esas sólo se ven en el texto original.
"""

from __future__ import annotations

import pytest

from dvu.domain.categorias import CATEGORIAS, clasificar, normalizar, slugs


@pytest.mark.parametrize(
    ("descripcion", "esperado"),
    [
        ("LIQUIDO DE FRENO FEDERAL", "automotriz"),
        ("ACEITE FEDERAL 2 CYCLE MOTOR OIL 1/4 GALON", "automotriz"),
        ("CERRADURA DE SOBREPONER", "cerrajeria"),
        ("CODO 90 PVC 110MM", "gasfiteria"),
        ("DISCO CORTE METAL 4 1/2", "abrasivos"),
        ("GUANTE CABRITILLA T-9", "seguridad"),
        ("CLAVO CORRIENTE 2 1/2", "fijaciones"),
        ("BROCHA 2 PULGADAS", "pinturas"),
        ("AMPOLLETA LED 9W", "electricidad"),
        ("ANZUELO MUSTAD N 8", "pesca"),
    ],
)
def test_clasifica_las_familias_del_catalogo(descripcion: str, esperado: str) -> None:
    assert clasificar(descripcion) == esperado


def test_lo_que_ninguna_regla_reconoce_queda_sin_categoria() -> None:
    """`None` es un resultado legítimo. Inventar una categoría rompe el árbol entero:
    el vendedor entra a buscar lo que sabe que existe y no está donde debería."""
    assert clasificar("POLICARBONATO ALVEOLAR BRONCE") is None
    assert clasificar("TAZÓN MÁGICO") is None


def test_las_tildes_no_cambian_el_resultado() -> None:
    """El catálogo escribe «REDUCCIÓN» y «REDUCCION» en la misma página."""
    assert clasificar("REDUCCIÓN PVC 110-75") == clasificar("REDUCCION PVC 110-75")
    assert clasificar("VÁLVULA DE BOLA") == "gasfiteria"


def test_el_plural_cae_en_la_misma_categoria() -> None:
    """«JGO DESTORNILLADOR 6 PZ» y «JGO DESTORNILLADORES DE PRECISION» conviven en el
    catálogo. Sin plurales, media familia queda sin clasificar."""
    assert clasificar("JGO DESTORNILLADOR 6 PZ") == "herramientas"
    assert clasificar("JGO DESTORNILLADORES DE PRECISION 6PZ") == "herramientas"
    assert clasificar("CLAVOS TECHO 3") == "fijaciones"


@pytest.mark.parametrize(
    ("descripcion", "esperado"),
    [
        ("LLAVE DE PASO 1/2", "gasfiteria"),
        ("LLAVE PUNTA CORONA 10MM", "herramientas"),
        ("LLAVE GASFITER 14", "herramientas"),
        ("CAJA P/INTERRUPTOR C/TAPA 2 MOD", "electricidad"),
        ("CAJA HERRAMIENTA 5C AZUL METALICA", "herramientas"),
    ],
)
def test_la_palabra_ambigua_la_desempata_el_resto_de_la_frase(
    descripcion: str, esperado: str
) -> None:
    """«LLAVE» es gasfitería o herramienta según lo que venga después, y las dos
    familias existen en el catálogo con decenas de productos cada una."""
    assert clasificar(descripcion) == esperado


def test_la_palabra_tiene_que_estar_completa() -> None:
    """Sin límite de palabra, «TEE» se comería «TEFLON» y «TERMINAL»."""
    assert clasificar("TEE PVC 110") == "gasfiteria"
    assert clasificar("CAMISETA ALGODON") is None


def test_no_hay_slugs_repetidos() -> None:
    """Dos categorías con el mismo slug harían que el filtro devuelva la mezcla."""
    assert len(set(slugs())) == len(CATEGORIAS)


def test_normalizar_deja_mayusculas_sin_tildes() -> None:
    assert normalizar("Reducción Ø") == "REDUCCION Ø"
