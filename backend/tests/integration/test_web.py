"""Prototipo web.

Las páginas son clientes de la API: no traen datos incrustados ni tienen sesión de
servidor. Eso es lo que se verifica acá — que sirvan, que apunten al prefijo real de la
API y que no filtren nada por venir del mismo proceso.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dvu.config import Settings

pytestmark = pytest.mark.integration

PAGINAS = ["/", "/ingresar", "/admin", "/pedido", "/vendedor", "/cobranza"]
PRIVADAS = ["/admin", "/pedido", "/vendedor", "/cobranza"]


@pytest.mark.parametrize("ruta", PAGINAS)
def test_las_paginas_se_sirven(cliente_api: TestClient, ruta: str) -> None:
    r = cliente_api.get(ruta)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("ruta", PAGINAS)
def test_la_pagina_apunta_al_prefijo_real_de_la_api(cliente_api: TestClient, ruta: str) -> None:
    """El JS lee el prefijo de este meta. Si no calza, la página queda muda."""
    assert '<meta name="dvu-api" content="/api/v1">' in cliente_api.get(ruta).text


@pytest.mark.parametrize("ruta", PRIVADAS)
def test_las_paginas_privadas_no_traen_datos_incrustados(
    cliente_api: TestClient, ruta: str
) -> None:
    """El HTML se sirve sin autenticar a propósito; los datos llegan después, con token.
    Si alguna vez alguien incrusta un listado en la plantilla, este test lo delata."""
    html = cliente_api.get(ruta).text

    assert 'id="app" hidden' in html
    assert "Cargando" in html or "Busca un producto" in html


@pytest.mark.parametrize("ruta", PRIVADAS)
def test_las_paginas_privadas_no_traen_su_propio_formulario_de_ingreso(
    cliente_api: TestClient, ruta: str
) -> None:
    """Ingresar es `/ingresar` y nada más.

    Cuando el formulario venía incrustado en cada página privada había cuatro logins que
    mantener y ninguno tenía dirección propia a la que mandar a alguien.
    """
    html = cliente_api.get(ruta).text

    assert 'id="form-login"' not in html
    assert 'id="sin-acceso"' in html


def test_la_pagina_de_ingreso_trae_el_formulario_en_un_modal(cliente_api: TestClient) -> None:
    html = cliente_api.get("/ingresar").text

    assert 'id="form-login"' in html
    assert "<dialog" in html


def test_la_barra_deja_el_hueco_de_la_sesion_en_toda_pagina(cliente_api: TestClient) -> None:
    """También en las públicas: sin el hueco no hay por dónde entrar."""
    for ruta in PAGINAS:
        assert 'id="sesion"' in cliente_api.get(ruta).text, ruta


def test_fuera_de_produccion_la_pantalla_de_ingreso_nombra_las_cuentas_de_ejemplo(
    cliente_api: TestClient,
) -> None:
    assert "dvu-dev-1234" in cliente_api.get("/ingresar").text


def test_en_produccion_la_pantalla_de_ingreso_no_nombra_ninguna_cuenta(
    cliente_api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Son las cuentas del `make seed`, con contraseña conocida.

    En producción no existen, pero escritas en la pantalla de ingreso son una lista de
    usuarios que probar; y si alguna vez alguien corre el seed contra producción, la
    contraseña queda publicada en la puerta.
    """
    monkeypatch.setattr("dvu.web.router.get_settings", lambda: Settings(env="production"))

    html = cliente_api.get("/ingresar").text

    assert "dvu-dev-1234" not in html
    assert "@dvu.cl" not in html
    # La página sigue sirviendo para entrar: lo que se va es la pista, no el formulario.
    assert 'id="form-login"' in html


def test_ninguna_plantilla_llama_a_crypto_random_uuid(cliente_api: TestClient) -> None:
    """`crypto.randomUUID` existe sólo en contexto seguro: HTTPS o localhost.

    La web se sirve por HTTP plano contra la IP del host, así que ahí no existe y armar
    una lista reventaba con «crypto.randomUUID is not a function» —en localhost, que es
    donde se programa, funcionaba siempre—. `DVU.uuid()` cae a `getRandomValues`, que
    está en todo contexto. Este test es la guarda: el próximo `client_uuid` que alguien
    escriba tiene que pasar por ahí.
    """
    for ruta in PAGINAS:
        assert "crypto.randomUUID" not in cliente_api.get(ruta).text, ruta

    js = cliente_api.get("/estatico/dvu.js").text
    # En el módulo sí se nombra, pero detrás de la comprobación que lo hace opcional.
    assert 'typeof crypto.randomUUID === "function"' in js
    assert "crypto.getRandomValues" in js


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
