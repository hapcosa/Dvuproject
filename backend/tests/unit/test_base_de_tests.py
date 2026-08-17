"""La fixture de base nunca apunta a la base del stack.

Esto no es una preferencia de nomenclatura. La fixture `engine` hace `drop_all` al
empezar **y al terminar**: si la URL configurada es la de `make up`, correr `pytest` se
lleva el catálogo cargado sin preguntar y sin aviso —el drop es lo último que corre,
después de que pytest ya imprimió que todo pasó—. Pasó una vez.
"""

from __future__ import annotations

import pytest

from tests.conftest import _url_de_test


@pytest.mark.parametrize(
    ("configurada", "esperada"),
    [
        (
            "postgresql+psycopg://dvu:dvu@db:5432/dvu",
            "postgresql+psycopg://dvu:dvu@db:5432/dvu_test",
        ),
        # Ya es de test: se deja como está, que es lo que usa CI.
        (
            "postgresql+psycopg://dvu:dvu@localhost:5432/dvu_test",
            "postgresql+psycopg://dvu:dvu@localhost:5432/dvu_test",
        ),
    ],
)
def test_la_url_de_tests_siempre_termina_en_test(configurada: str, esperada: str) -> None:
    assert _url_de_test(configurada) == esperada


def test_los_parametros_de_conexion_se_conservan() -> None:
    """El sufijo se le pone a la base, no a la query: perder el `sslmode` deja los tests
    sin poder conectarse contra un Postgres que lo exige."""
    url = _url_de_test("postgresql+psycopg://dvu:dvu@db:5432/dvu?sslmode=require")

    assert url == "postgresql+psycopg://dvu:dvu@db:5432/dvu_test?sslmode=require"
