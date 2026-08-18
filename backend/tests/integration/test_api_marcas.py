"""Marcas del catálogo.

Lo que se cuida acá es el trabajo humano. En el impreso la marca es el logo del
proveedor y el extractor sólo puede recortarlo: son imágenes anónimas hasta que alguien
las nombra, y ese nombre no puede perderse en la próxima recarga del PDF ni pisarse solo
cuando se adopta el segundo recorte de la misma marca.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.almacenamiento import AlmacenLocal, get_almacen
from dvu.api.main import create_app
from dvu.db.models import Marca, Producto, Usuario
from dvu.db.session import get_session
from dvu.seguridad import emitir_token, hashear

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"

#: Dos recortes distintos de la misma marca. Es el caso real: la misma marca sale en
#: varias páginas y cada recorte tiene su hash, así que llega como dos logos.
LOGO_A = "catalogo/marcas/3d587bc1bb536d16.jpeg"
LOGO_B = "catalogo/marcas/9bb2305c17610a9a.jpeg"


@pytest.fixture
def datos(sesion: Session) -> dict[str, Any]:
    admin = Usuario(email="a@test.cl", nombre="A", rol="admin", password_hash=hashear("x"))
    editor = Usuario(email="e@test.cl", nombre="E", rol="editor", password_hash=hashear("x"))
    vendedor = Usuario(email="v@test.cl", nombre="V", rol="vendedor", password_hash=hashear("x"))
    sesion.add_all([admin, editor, vendedor])
    sesion.flush()

    for indice, logo in enumerate([LOGO_A, LOGO_A, LOGO_A, LOGO_B]):
        sesion.add(
            Producto(
                sku=f"DVU-{indice}",
                descripcion=f"CODO 90 PVC {indice}",
                multiplo_venta=12,
                unidad_venta="UNID",
                precio_lista_clp=Decimal("1790"),
                marca_logo_key=logo,
            )
        )
    # Sin logo: el extractor no le recortó nada. No tiene por qué aparecer en la
    # lista de trabajo.
    sesion.add(
        Producto(
            sku="DVU-SIN-LOGO",
            descripcion="POLICARBONATO ALVEOLAR",
            multiplo_venta=1,
            unidad_venta="UNID",
            precio_lista_clp=Decimal("9990"),
        )
    )
    sesion.flush()

    return {
        "auth_admin": {"Authorization": f"Bearer {emitir_token(admin.uuid, 'admin')}"},
        "auth_editor": {"Authorization": f"Bearer {emitir_token(editor.uuid, 'editor')}"},
        "auth_vendedor": {"Authorization": f"Bearer {emitir_token(vendedor.uuid, 'vendedor')}"},
    }


@pytest.fixture
def cliente_api(sesion: Session) -> Any:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: sesion
    with TestClient(app) as cliente:
        yield cliente
    app.dependency_overrides.clear()


@pytest.fixture
def cliente_api_local(sesion: Session, tmp_path: Any) -> Any:
    """Cliente con almacén en disco: los tests no necesitan MinIO."""
    app = create_app()
    app.dependency_overrides[get_session] = lambda: sesion
    app.dependency_overrides[get_almacen] = lambda: AlmacenLocal(tmp_path)
    with TestClient(app) as cliente:
        yield cliente
    app.dependency_overrides.clear()


def _crear(cliente: TestClient, datos: dict[str, Any], nombre: str) -> dict[str, Any]:
    r = cliente.post(f"{PREFIJO}/marcas", json={"nombre": nombre}, headers=datos["auth_editor"])
    assert r.status_code == 201, r.text
    return dict(r.json())


# --- la lista de trabajo ----------------------------------------------------


def test_los_logos_sin_marca_salen_del_mas_usado_al_menos(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """El reparto es muy desparejo y conviene empezar por donde rinde."""
    r = cliente_api.get(f"{PREFIJO}/marcas/logos-sin-marca", headers=datos["auth_editor"])

    assert r.status_code == 200
    filas = r.json()
    assert [f["logo_key"] for f in filas] == [LOGO_A, LOGO_B]
    assert [f["productos"] for f in filas] == [3, 1]


def test_el_producto_sin_logo_no_aparece_en_la_lista_de_trabajo(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """No hay nada que nombrar: el extractor no le recortó ninguna imagen."""
    r = cliente_api.get(f"{PREFIJO}/marcas/logos-sin-marca", headers=datos["auth_editor"])

    assert all(f["logo_key"] is not None for f in r.json())
    assert sum(f["productos"] for f in r.json()) == 4


def test_la_lista_de_trabajo_no_es_publica(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    assert cliente_api.get(f"{PREFIJO}/marcas/logos-sin-marca").status_code == 401
    r = cliente_api.get(f"{PREFIJO}/marcas/logos-sin-marca", headers=datos["auth_vendedor"])
    assert r.status_code == 403


# --- adoptar ----------------------------------------------------------------


def test_adoptar_un_logo_le_pone_la_marca_a_todos_sus_productos(
    cliente_api: TestClient, datos: dict[str, Any], sesion: Session
) -> None:
    """El paso que convierte una imagen anónima en una marca de verdad."""
    _crear(cliente_api, datos, "Vinilit")

    r = cliente_api.post(
        f"{PREFIJO}/marcas/vinilit/adoptar",
        json={"logo_key": LOGO_A},
        headers=datos["auth_editor"],
    )

    assert r.status_code == 200
    assert r.json()["productos_asignados"] == 3
    assert r.json()["marca"]["productos"] == 3
    productos = sesion.scalars(select(Producto).where(Producto.marca_logo_key == LOGO_A)).all()
    assert all(p.marca is not None and p.marca.nombre == "Vinilit" for p in productos)


def test_la_marca_sin_logo_se_queda_con_el_del_recorte_que_adopta(
    cliente_api: TestClient, datos: dict[str, Any], sesion: Session
) -> None:
    """El logo del impreso es el logo del proveedor: no hay que volver a subirlo."""
    _crear(cliente_api, datos, "Vinilit")
    assert cliente_api.get(f"{PREFIJO}/marcas").json()[0]["tiene_logo"] is False

    cliente_api.post(
        f"{PREFIJO}/marcas/vinilit/adoptar",
        json={"logo_key": LOGO_A},
        headers=datos["auth_editor"],
    )

    assert sesion.scalars(select(Marca)).one().logo_key == LOGO_A


def test_dos_recortes_de_la_misma_marca_caen_en_la_misma_marca(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """Así se juntan los duplicados: la misma marca sale recortada distinto en cada
    página y llega como dos logos, pero es una sola marca."""
    _crear(cliente_api, datos, "Vinilit")
    for logo in (LOGO_A, LOGO_B):
        cliente_api.post(
            f"{PREFIJO}/marcas/vinilit/adoptar",
            json={"logo_key": logo},
            headers=datos["auth_editor"],
        )

    assert cliente_api.get(f"{PREFIJO}/marcas").json()[0]["productos"] == 4
    assert (
        cliente_api.get(f"{PREFIJO}/marcas/logos-sin-marca", headers=datos["auth_editor"]).json()
        == []
    )


def test_adoptar_no_le_saca_la_marca_a_un_producto_que_ya_tiene(
    cliente_api: TestClient, datos: dict[str, Any], sesion: Session
) -> None:
    """Sólo llena vacíos. Lo que alguien ya decidió no se deshace por adoptar otro
    recorte que coincida."""
    _crear(cliente_api, datos, "Vinilit")
    _crear(cliente_api, datos, "Tigre")
    cliente_api.post(
        f"{PREFIJO}/marcas/vinilit/adoptar",
        json={"logo_key": LOGO_A},
        headers=datos["auth_editor"],
    )

    r = cliente_api.post(
        f"{PREFIJO}/marcas/tigre/adoptar",
        json={"logo_key": LOGO_A},
        headers=datos["auth_editor"],
    )

    assert r.status_code == 404
    productos = sesion.scalars(select(Producto).where(Producto.marca_logo_key == LOGO_A)).all()
    assert all(p.marca is not None and p.marca.nombre == "Vinilit" for p in productos)


def test_adoptar_es_del_editor_y_no_del_vendedor(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    _crear(cliente_api, datos, "Vinilit")
    r = cliente_api.post(
        f"{PREFIJO}/marcas/vinilit/adoptar",
        json={"logo_key": LOGO_A},
        headers=datos["auth_vendedor"],
    )
    assert r.status_code == 403


# --- crear y nombrar --------------------------------------------------------


def test_el_nombre_con_tildes_y_sin_tildes_es_la_misma_marca(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """Si no chocaran quedarían dos marcas para el mismo proveedor, que es justamente
    lo que hay que evitar."""
    _crear(cliente_api, datos, "Cementos Bío-Bío")

    r = cliente_api.post(
        f"{PREFIJO}/marcas", json={"nombre": "Cementos Bio Bio"}, headers=datos["auth_editor"]
    )

    assert r.status_code == 409


def test_un_nombre_que_no_deja_slug_se_rechaza(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    r = cliente_api.post(f"{PREFIJO}/marcas", json={"nombre": "---"}, headers=datos["auth_editor"])

    assert r.status_code == 422


def test_el_catalogo_lista_las_marcas_sin_sesion(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """Es la vitrina, como el resto del catálogo."""
    _crear(cliente_api, datos, "Vinilit")

    r = cliente_api.get(f"{PREFIJO}/marcas")

    assert r.status_code == 200
    assert r.json()[0]["nombre"] == "Vinilit"


def test_crear_marcas_no_es_del_vendedor(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/marcas", json={"nombre": "Vinilit"}, headers=datos["auth_vendedor"]
    )
    assert r.status_code == 403


# --- el logo ----------------------------------------------------------------


def test_subir_un_logo_lo_deja_servido_sin_sesion(
    cliente_api_local: TestClient, datos: dict[str, Any]
) -> None:
    """Para la marca que no está en el impreso y para el recorte que salió cortado."""
    cliente_api_local.post(
        f"{PREFIJO}/marcas", json={"nombre": "Vinilit"}, headers=datos["auth_editor"]
    )

    r = cliente_api_local.post(
        f"{PREFIJO}/marcas/vinilit/logo",
        files={"archivo": ("logo.png", io.BytesIO(b"\x89PNG datos"), "image/png")},
        headers=datos["auth_editor"],
    )

    assert r.status_code == 200
    assert r.json()["tiene_logo"] is True
    assert cliente_api_local.get(f"{PREFIJO}/marcas/vinilit/logo").status_code == 200


def test_el_logo_subido_no_pisa_el_recorte_del_extractor(
    cliente_api_local: TestClient, datos: dict[str, Any], sesion: Session
) -> None:
    """Prefijo propio: `marcas/`, y no `catalogo/marcas/` donde el extractor deja los
    suyos con nombre de hash."""
    cliente_api_local.post(
        f"{PREFIJO}/marcas", json={"nombre": "Vinilit"}, headers=datos["auth_editor"]
    )
    cliente_api_local.post(
        f"{PREFIJO}/marcas/vinilit/logo",
        files={"archivo": ("logo.png", io.BytesIO(b"\x89PNG datos"), "image/png")},
        headers=datos["auth_editor"],
    )

    key = sesion.scalars(select(Marca)).one().logo_key
    assert key is not None
    assert key.startswith("marcas/")
    assert not key.startswith("catalogo/")


def test_una_marca_sin_logo_responde_404_y_no_una_imagen_rota(
    cliente_api_local: TestClient, datos: dict[str, Any]
) -> None:
    cliente_api_local.post(
        f"{PREFIJO}/marcas", json={"nombre": "Vinilit"}, headers=datos["auth_editor"]
    )

    assert cliente_api_local.get(f"{PREFIJO}/marcas/vinilit/logo").status_code == 404


def test_un_archivo_que_no_es_imagen_no_entra(
    cliente_api_local: TestClient, datos: dict[str, Any]
) -> None:
    cliente_api_local.post(
        f"{PREFIJO}/marcas", json={"nombre": "Vinilit"}, headers=datos["auth_editor"]
    )

    r = cliente_api_local.post(
        f"{PREFIJO}/marcas/vinilit/logo",
        files={"archivo": ("virus.exe", io.BytesIO(b"MZ"), "application/x-msdownload")},
        headers=datos["auth_editor"],
    )

    assert r.status_code == 415


# --- lo que la recarga del PDF no puede deshacer -----------------------------


def test_recargar_el_catalogo_no_borra_la_marca_que_alguien_nombro(
    cliente_api: TestClient, datos: dict[str, Any], sesion: Session
) -> None:
    """La razón por la que la marca vive en su propia tabla.

    `_aplicar` reescribe la salida del extractor en cada pasada del PDF sin condición.
    Si la marca curada viviera en esas columnas, cada `make cargar-catalogo` borraría
    el trabajo del editor sin dejar rastro.
    """
    from dvu.carga.catalogo import _aplicar

    _crear(cliente_api, datos, "Vinilit")
    cliente_api.post(
        f"{PREFIJO}/marcas/vinilit/adoptar",
        json={"logo_key": LOGO_A},
        headers=datos["auth_editor"],
    )
    producto = sesion.scalars(select(Producto).where(Producto.sku == "DVU-0")).one()

    _aplicar(
        producto,
        {
            "descripcion": "CODO 90 PVC 110MM",
            "marca": '1/2"',
            "medida": {"texto": "110MM", "valor": None, "unidad": None},
            "venta_minima": {"unidad": "UNID", "multiplo": 12, "envase": None},
            "precio_clp": 1790,
        },
    )
    sesion.flush()

    assert producto.marca is not None
    assert producto.marca.nombre == "Vinilit"
    assert producto.marca_impresa == '1/2"'
