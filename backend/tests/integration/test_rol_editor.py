"""El rol `editor`: mantiene el catálogo y lo imprime, y nada más.

El catálogo lo cuida alguien que no tiene por qué ver cobranza ni facturación. Hasta
ahora la única forma de dejar editar era dar `admin`, o sea entregar también la bandeja
de pagos, el SII y el reparto de accesos.

La mitad importante de este archivo es la de abajo: **lo que el editor no puede**. Un rol
nuevo se agrega mirando lo que habilita; lo que se olvida es lo que deja abierto.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from dvu.db.models import Categoria, Producto, Usuario
from dvu.domain.roles import ADMIN, EDITOR, VENDEDOR
from dvu.seguridad import emitir_token, hashear

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"


@pytest.fixture
def escenario(sesion: Session) -> dict[str, Any]:
    editor = Usuario(email="e@test.cl", nombre="Editor", rol=EDITOR, password_hash=hashear("x"))
    admin = Usuario(email="a@test.cl", nombre="Admin", rol=ADMIN, password_hash=hashear("x"))
    vendedor = Usuario(email="v@test.cl", nombre="Vend", rol=VENDEDOR, password_hash=hashear("x"))
    sesion.add_all([editor, admin, vendedor])

    producto = Producto(
        sku="DVU-TEST1",
        descripcion="CERRADURA DE PRUEBA",
        multiplo_venta=6,
        unidad_venta="UNID",
        precio_lista_clp=Decimal("9990"),
    )
    sesion.add(producto)
    sesion.flush()

    return {
        "producto": producto,
        "editor": {"Authorization": f"Bearer {emitir_token(editor.uuid, EDITOR)}"},
        "vendedor": {"Authorization": f"Bearer {emitir_token(vendedor.uuid, VENDEDOR)}"},
    }


# --- lo que el editor sí puede ------------------------------------------------


def test_el_editor_corrige_un_producto(cliente_api: TestClient, escenario: dict[str, Any]) -> None:
    """Es para lo que existe el rol: lo que el extractor leyó mal se arregla a mano."""
    r = cliente_api.patch(
        f"{PREFIJO}/productos/{escenario['producto'].sku}",
        json={"descripcion": "CERRADURA CORREGIDA"},
        headers=escenario["editor"],
    )

    assert r.status_code == 200
    assert r.json()["descripcion"] == "CERRADURA CORREGIDA"


def test_el_editor_crea_un_producto(cliente_api: TestClient, escenario: dict[str, Any]) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/productos",
        json={
            "sku": "DVU-NUEVO1",
            "descripcion": "PRODUCTO NUEVO",
            "multiplo_venta": 1,
            "unidad_venta": "UNID",
            "precio_lista_clp": 1000,
        },
        headers=escenario["editor"],
    )

    assert r.status_code == 201


def test_el_editor_administra_categorias(
    cliente_api: TestClient, escenario: dict[str, Any], sesion: Session
) -> None:
    sesion.add(Categoria(slug="fijaciones", nombre="Fijaciones"))
    sesion.flush()

    r = cliente_api.patch(
        f"{PREFIJO}/categorias/fijaciones",
        json={"nombre": "Fijaciones y anclajes"},
        headers=escenario["editor"],
    )

    assert r.status_code == 200


def test_el_editor_imprime_el_catalogo(cliente_api: TestClient, escenario: dict[str, Any]) -> None:
    """«Editar el catálogo e imprimirlo» es el encargo entero: sin esto falta la mitad."""
    r = cliente_api.get(
        f"{PREFIJO}/reportes/catalogo.pdf?con_imagenes=false", headers=escenario["editor"]
    )

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"


# --- lo que el editor NO puede ------------------------------------------------


@pytest.mark.parametrize(
    "metodo,ruta",
    [
        ("GET", "/reportes/ventas.xlsx"),
        ("GET", "/conciliacion/movimientos"),
        ("GET", "/comprobantes"),
        ("GET", "/usuarios"),
        ("GET", "/pagos"),
    ],
)
def test_el_editor_no_ve_la_plata(
    cliente_api: TestClient, escenario: dict[str, Any], metodo: str, ruta: str
) -> None:
    """Cobranza, facturación, comprobantes y accesos quedan fuera. Ese es el punto de que
    el rol exista: antes había que dar `admin` para dejar editar el catálogo."""
    r = cliente_api.request(metodo, f"{PREFIJO}{ruta}", headers=escenario["editor"])

    assert r.status_code == 403, f"{metodo} {ruta} no debería estar abierto al editor"


def test_los_comprobantes_no_se_ven_por_tener_sesion(
    cliente_api: TestClient, escenario: dict[str, Any]
) -> None:
    """Traen monto, banco y número de operación del cliente.

    `GET /comprobantes` sólo acotaba la consulta cuando el rol era `vendedor`, así que
    cualquier otro rol autenticado los veía todos. Con un solo rol más eso pasó de puerta
    entornada a puerta con cartel.
    """
    assert (
        cliente_api.get(f"{PREFIJO}/comprobantes", headers=escenario["editor"]).status_code == 403
    )
    # El vendedor sí, y sólo los suyos: eso lo cubre test_api_comprobantes.
    assert (
        cliente_api.get(f"{PREFIJO}/comprobantes", headers=escenario["vendedor"]).status_code == 200
    )
