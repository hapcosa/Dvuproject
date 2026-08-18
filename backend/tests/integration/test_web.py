"""Prototipo web.

Las páginas son clientes de la API: no traen datos incrustados ni tienen sesión de
servidor. Eso es lo que se verifica acá — que sirvan, que apunten al prefijo real de la
API y que no filtren nada por venir del mismo proceso.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from dvu.config import Settings
from dvu.web import router

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


@pytest.mark.parametrize("ruta", ["/", "/pedido"])
def test_el_carrito_va_en_el_catalogo_y_en_pedido(cliente_api: TestClient, ruta: str) -> None:
    """La lista se arma desde donde se está mirando el producto.

    Antes sólo se podía en /pedido: el vendedor que estaba en el catálogo con el
    ferretero al lado tenía que cambiar de página, perder la búsqueda y encontrar el
    producto de nuevo.
    """
    html = cliente_api.get(ruta).text

    assert 'id="carrito"' in html
    assert 'id="carrito-boton"' in html


def test_el_carrito_se_minimiza_y_no_se_cierra(cliente_api: TestClient) -> None:
    """Minimizar corre el panel; la lista no se pierde ni se descarta.

    El panel tapaba la foto y la fila que el vendedor le está mostrando al ferretero.
    Cerrarlo del todo no existe porque no significaría nada distinto de minimizarlo.
    """
    html = cliente_api.get("/").text

    assert 'id="carrito-minimizar"' in html
    assert 'id="carrito-cerrar"' not in html


def test_el_carrito_no_oscurece_el_catalogo_en_pantalla_ancha(cliente_api: TestClient) -> None:
    """El panel es un costado, no un modal: se sigue buscando y mirando fotos con él
    abierto. El fondo oscuro queda sólo para el celular, donde ocupa la pantalla entera."""
    css = cliente_api.get("/estatico/dvu.css").text

    assert ".carrito-fondo { display: none; }" in css
    # Y la hoja se corre para que el panel no quede encima de la tabla.
    assert "con-carrito" in css


def test_el_carrito_no_se_cuela_donde_no_se_pide(cliente_api: TestClient) -> None:
    """Cobranza y administración no arman pedidos; un carrito ahí es un adorno que tapa."""
    for ruta in ["/admin", "/cobranza", "/vendedor", "/ingresar"]:
        assert 'id="carrito"' not in cliente_api.get(ruta).text, ruta


def test_pedido_ya_no_trae_la_barra_de_abajo(cliente_api: TestClient) -> None:
    """El drawer hace ese papel, y ahora en las dos páginas.

    Dejar las dos serían tres vistas de la misma lista —barra, tarjeta y drawer— con tres
    sitios donde acordarse de repintar.
    """
    html = cliente_api.get("/pedido").text

    assert "barra-pedido" not in html
    assert "barra-pedido" not in cliente_api.get("/estatico/dvu.css").text


def test_el_catalogo_trae_la_columna_de_pedir(cliente_api: TestClient) -> None:
    """Nace oculta: la muestra el JS sólo si el rol de la sesión puede pedir. El HTML se
    sirve igual para todos porque no hay sesión de servidor."""
    html = cliente_api.get("/").text

    assert 'id="th-pedir" hidden' in html


def test_el_estado_de_la_lista_vive_en_un_solo_lugar(cliente_api: TestClient) -> None:
    """El carrito lo tiene `dvu.js`, no cada plantilla.

    Cuando /pedido guardaba su propia copia no había con qué compartirla: el catálogo
    habría necesitado una segunda, y la que se desincroniza es la que se está mirando.
    """
    js = cliente_api.get("/estatico/dvu.js").text
    assert "carrito.montar" in js

    # Las plantillas leen del carrito; no declaran su propio almacén de listas.
    for ruta in ["/", "/pedido"]:
        html = cliente_api.get(ruta).text
        assert "const enServidor = {" not in html, ruta
        assert "const enNavegador = {" not in html, ruta


@pytest.mark.parametrize("ruta", PAGINAS)
def test_los_estaticos_van_con_huella_en_la_url(cliente_api: TestClient, ruta: str) -> None:
    """`StaticFiles` no manda `cache-control`, así que el navegador cachea por heurística
    y ni siquiera revalida: se desplegó el panel del carrito y en pantalla seguía el de
    antes, sin nada que lo dijera. Con la huella en la URL, un archivo distinto es una
    URL distinta."""
    html = cliente_api.get(ruta).text

    assert re.search(r'/estatico/dvu\.css\?v=[0-9a-f]{12}"', html), ruta
    assert re.search(r'/estatico/dvu\.js\?v=[0-9a-f]{12}"', html), ruta


def test_la_huella_cambia_cuando_cambia_el_archivo(tmp_path: Path) -> None:
    """Si no cambiara, la URL sería estable y el navegador seguiría con la copia vieja:
    justo lo que se venía a arreglar."""
    archivo = tmp_path / "dvu.css"
    archivo.write_text("a{}")

    with patch.object(router, "ESTATICOS", (archivo,)):
        antes = router.version_estaticos()
        archivo.write_text("a{color:red}")
        despues = router.version_estaticos()

    assert antes != despues
    assert len(antes) == 12


def test_el_atributo_hidden_le_gana_al_display(cliente_api: TestClient) -> None:
    """Sin esto, esconder algo con `hidden` no lo esconde.

    La hoja del navegador trae `[hidden] { display: none }`, pero cualquier `display:` de
    `dvu.css` le gana por ser CSS de autor. Le bastó `display: flex` al panel del carrito
    para quedar visible para siempre tapando el catálogo, con el JS poniéndole
    `hidden = true` correctamente: se veía clavado y «minimizar» parecía no existir.

    Se comprueba acá y no en los tests de navegador porque jsdom aplica `hidden` con más
    fuerza de la que le toca —responde `none` donde un navegador responde `flex`— y daría
    el visto bueno a esa misma pantalla rota. Sobre el texto de la hoja la respuesta es
    determinista.
    """
    css = cliente_api.get("/estatico/dvu.css").text

    assert re.search(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", css), (
        "falta la regla global `[hidden] { display: none !important }`"
    )


def test_la_pantalla_de_usuarios_nace_oculta(cliente_api: TestClient) -> None:
    """La muestra el JS sólo para `admin`. El editor entra a /admin a mantener el
    catálogo y no tiene por qué ver el reparto de accesos; quien lo cierra de verdad es
    el servidor, que le responde 403 a `/usuarios`."""
    html = cliente_api.get("/admin").text

    assert 'id="panel-usuarios" hidden' in html
    assert 'id="form-usuario"' in html


def test_admin_lo_abre_el_editor_tambien(cliente_api: TestClient) -> None:
    """`alcanza()` deja pasar a `admin` siempre, igual que `exige_rol`, así que
    `["editor"]` se lee «editor o administrador». Si esto dijera `["admin"]`, el editor
    vería «esta página no es para tu cuenta» sobre la página que sí le toca."""
    js = cliente_api.get("/estatico/dvu.js").text

    assert 'ruta: "/admin", nombre: "Administrar catálogo", roles: ["editor"]' in js


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
