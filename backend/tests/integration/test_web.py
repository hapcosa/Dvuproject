"""Prototipo web.

Las páginas son clientes de la API: no traen datos incrustados ni tienen sesión de
servidor. Eso es lo que se verifica acá — que sirvan, que apunten al prefijo real de la
API y que no filtren nada por venir del mismo proceso.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PAGINAS = ["/", "/admin", "/vendedor", "/cobranza"]


@pytest.mark.parametrize("ruta", PAGINAS)
def test_las_paginas_se_sirven(cliente_api: TestClient, ruta: str) -> None:
    r = cliente_api.get(ruta)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("ruta", PAGINAS)
def test_la_pagina_apunta_al_prefijo_real_de_la_api(cliente_api: TestClient, ruta: str) -> None:
    """El JS lee el prefijo de este meta. Si no calza, la página queda muda."""
    assert '<meta name="dvu-api" content="/api/v1">' in cliente_api.get(ruta).text


@pytest.mark.parametrize("ruta", ["/admin", "/cobranza", "/vendedor"])
def test_las_paginas_privadas_no_traen_datos_incrustados(
    cliente_api: TestClient, ruta: str
) -> None:
    """El HTML se sirve sin autenticar a propósito; los datos llegan después, con token.
    Si alguna vez alguien incrusta un listado en la plantilla, este test lo delata."""
    html = cliente_api.get(ruta).text

    assert 'id="login"' in html
    assert "Cargando" in html or "Busca un producto" in html


def test_los_estaticos_se_sirven(cliente_api: TestClient) -> None:
    css = cliente_api.get("/estatico/dvu.css")
    js = cliente_api.get("/estatico/dvu.js")

    assert css.status_code == 200
    assert js.status_code == 200
    # El token vive en sessionStorage, no en localStorage: se borra al cerrar la
    # pestaña, que en un equipo compartido de bodega es la diferencia que importa.
    assert "sessionStorage" in js.text
    assert "localStorage.setItem" not in js.text


def test_la_web_no_tapa_la_api(cliente_api: TestClient) -> None:
    assert cliente_api.get("/api/v1/productos").status_code == 200
    assert cliente_api.get("/health").status_code == 200


def test_las_paginas_no_aparecen_en_el_openapi(cliente_api: TestClient) -> None:
    """El contrato publicado es el de la API. Las páginas no son parte de él."""
    rutas = cliente_api.get("/openapi.json").json()["paths"]

    assert not any(ruta in rutas for ruta in PAGINAS if ruta != "/")
