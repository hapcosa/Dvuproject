"""Edición del catálogo por el administrador.

La carga masiva viene del PDF (`make cargar-catalogo`). Esto es la corrección a mano de
lo que el extractor leyó mal, y lo que se cuida acá es que corregir no destruya: un
producto se desactiva, nunca se borra, porque está referenciado en pedidos históricos.
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
from dvu.db.models import Producto, ProductoAlias, Usuario
from dvu.db.session import get_session
from dvu.seguridad import emitir_token, hashear

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"


@pytest.fixture
def datos(sesion: Session) -> dict[str, Any]:
    admin = Usuario(email="a@test.cl", nombre="A", rol="admin", password_hash=hashear("x"))
    vendedor = Usuario(email="v@test.cl", nombre="V", rol="vendedor", password_hash=hashear("x"))
    sesion.add_all([admin, vendedor])
    sesion.flush()

    producto = Producto(
        sku="DVU-PR49573",
        descripcion="LIQUIDO DE FRENO FEDERAL",
        multiplo_venta=12,
        unidad_venta="UNID",
        precio_lista_clp=Decimal("1790"),
        alias=[ProductoAlias(codigo="PR/49573", origen="catalogo")],
    )
    sesion.add(producto)
    sesion.flush()

    return {
        "producto": producto,
        "auth_admin": {"Authorization": f"Bearer {emitir_token(admin.uuid, 'admin')}"},
        "auth_vendedor": {"Authorization": f"Bearer {emitir_token(vendedor.uuid, 'vendedor')}"},
    }


def _nuevo(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sku": "DVU-KM521",
        "descripcion": "CERRADURA DE SOBREPONER",
        "precio_lista_clp": 12_990,
        "multiplo_venta": 6,
        "envase": "CAJA",
        "codigos_proveedor": ["KM521"],
    }
    base.update(extra)
    return base


def test_crear_producto(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    r = cliente_api.post(f"{PREFIJO}/productos", json=_nuevo(), headers=datos["auth_admin"])

    assert r.status_code == 201
    cuerpo = r.json()
    assert cuerpo["sku"] == "DVU-KM521"
    assert cuerpo["multiplo_venta"] == 6
    assert cuerpo["codigos_proveedor"] == ["KM521"]


def test_el_sku_duplicado_no_pisa_el_existente(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/productos", json=_nuevo(sku="DVU-PR49573"), headers=datos["auth_admin"]
    )

    assert r.status_code == 409


def test_el_multiplo_de_venta_no_puede_ser_cero(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """DVU vende por caja. Un múltiplo 0 haría que el carrito acepte cualquier cantidad."""
    r = cliente_api.post(
        f"{PREFIJO}/productos", json=_nuevo(multiplo_venta=0), headers=datos["auth_admin"]
    )

    assert r.status_code == 422


def test_el_precio_no_puede_ser_negativo(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/productos", json=_nuevo(precio_lista_clp=-1), headers=datos["auth_admin"]
    )

    assert r.status_code == 422


def test_corregir_precio_y_descripcion(
    cliente_api: TestClient, datos: dict[str, Any], sesion: Session
) -> None:
    r = cliente_api.patch(
        f"{PREFIJO}/productos/DVU-PR49573",
        json={"precio_lista_clp": 1990, "descripcion": "LIQUIDO DE FRENO FEDERAL 500ML"},
        headers=datos["auth_admin"],
    )

    assert r.status_code == 200
    assert r.json()["precio_lista_clp"] == 1990
    # Entero, no float: el precio en CLP no tiene decimales en ninguna capa.
    assert sesion.scalars(select(Producto)).one().precio_lista_clp == Decimal("1990")


def test_lo_que_no_viene_en_el_parche_no_se_toca(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    r = cliente_api.patch(
        f"{PREFIJO}/productos/DVU-PR49573",
        json={"marca": "FEDERAL"},
        headers=datos["auth_admin"],
    )

    assert r.json()["marca"] == "FEDERAL"
    assert r.json()["multiplo_venta"] == 12
    assert r.json()["descripcion"] == "LIQUIDO DE FRENO FEDERAL"


def test_desactivar_saca_del_catalogo_sin_borrar(
    cliente_api: TestClient, datos: dict[str, Any], sesion: Session
) -> None:
    """La ficha puede estar referenciada en pedidos viejos: se apaga, no se elimina."""
    cliente_api.patch(
        f"{PREFIJO}/productos/DVU-PR49573", json={"activo": False}, headers=datos["auth_admin"]
    )

    assert cliente_api.get(f"{PREFIJO}/productos").json()["total"] == 0
    assert sesion.scalars(select(Producto)).one().sku == "DVU-PR49573"


def test_agregar_alias_permite_buscar_por_el_codigo_del_proveedor(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """El vendedor busca por el código que tenga a mano, y conviven cinco formatos."""
    r = cliente_api.post(
        f"{PREFIJO}/productos/DVU-PR49573/alias",
        params={"codigo": "080633000-T"},
        headers=datos["auth_admin"],
    )

    assert r.status_code == 201
    assert "080633000-T" in r.json()["codigos_proveedor"]

    encontrado = cliente_api.get(f"{PREFIJO}/productos", params={"q": "080633000-T"}).json()
    assert encontrado["total"] == 1


def test_agregar_el_mismo_alias_dos_veces_es_inocuo(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    cliente_api.post(
        f"{PREFIJO}/productos/DVU-PR49573/alias",
        params={"codigo": "ASK11003"},
        headers=datos["auth_admin"],
    )
    r = cliente_api.post(
        f"{PREFIJO}/productos/DVU-PR49573/alias",
        params={"codigo": "ASK11003"},
        headers=datos["auth_admin"],
    )

    assert r.status_code == 201
    assert r.json()["codigos_proveedor"].count("ASK11003") == 1


def test_producto_inexistente_da_404(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    r = cliente_api.patch(
        f"{PREFIJO}/productos/NO-EXISTE", json={"marca": "X"}, headers=datos["auth_admin"]
    )

    assert r.status_code == 404


@pytest.mark.parametrize(
    ("metodo", "ruta", "cuerpo"),
    [
        ("post", "/productos", {"sku": "X", "descripcion": "X", "precio_lista_clp": 1}),
        ("patch", "/productos/DVU-PR49573", {"marca": "X"}),
    ],
)
def test_el_vendedor_no_edita_el_catalogo(
    cliente_api: TestClient, datos: dict[str, Any], metodo: str, ruta: str, cuerpo: dict[str, Any]
) -> None:
    r = getattr(cliente_api, metodo)(
        f"{PREFIJO}{ruta}", json=cuerpo, headers=datos["auth_vendedor"]
    )

    assert r.status_code == 403


def test_el_catalogo_se_lee_sin_ingresar(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    """El catálogo es la vitrina: se ve sin cuenta. Editarlo no."""
    r = cliente_api.get(f"{PREFIJO}/productos")

    assert r.status_code == 200
    assert r.json()["total"] == 1


@pytest.fixture
def cliente_api_local(sesion: Session, tmp_path: Any) -> Any:
    """Cliente HTTP con almacén en disco: los tests no necesitan MinIO."""
    app = create_app()
    app.dependency_overrides[get_session] = lambda: sesion
    app.dependency_overrides[get_almacen] = lambda: AlmacenLocal(tmp_path)
    with TestClient(app) as cliente:
        yield cliente
    app.dependency_overrides.clear()


def _foto(nombre: str = "foto.png", tipo: str = "image/png") -> dict[str, Any]:
    return {"archivo": (nombre, io.BytesIO(b"\x89PNG datos de imagen"), tipo)}


def test_subir_la_foto_del_producto(
    cliente_api_local: TestClient, datos: dict[str, Any], sesion: Session
) -> None:
    r = cliente_api_local.post(
        f"{PREFIJO}/productos/DVU-PR49573/imagen", files=_foto(), headers=datos["auth_admin"]
    )

    assert r.status_code == 200
    assert r.json()["imagen_key"] == "catalogo/DVU-PR49573.png"

    vista = cliente_api_local.get(f"{PREFIJO}/productos/DVU-PR49573/imagen", follow_redirects=False)
    assert vista.status_code == 200
    assert vista.content == b"\x89PNG datos de imagen"


def test_resubir_la_foto_pisa_la_anterior(
    cliente_api_local: TestClient, datos: dict[str, Any]
) -> None:
    """Reemplazar una foto mala no puede ir dejando objetos huérfanos en el bucket."""
    primera = cliente_api_local.post(
        f"{PREFIJO}/productos/DVU-PR49573/imagen", files=_foto(), headers=datos["auth_admin"]
    ).json()
    segunda = cliente_api_local.post(
        f"{PREFIJO}/productos/DVU-PR49573/imagen",
        files=_foto("otra.png"),
        headers=datos["auth_admin"],
    ).json()

    assert primera["imagen_key"] == segunda["imagen_key"]


def test_el_pdf_no_sirve_como_foto_de_catalogo(
    cliente_api_local: TestClient, datos: dict[str, Any]
) -> None:
    """El comprobante sí acepta PDF; la foto del catálogo va en un `<img>` y no."""
    r = cliente_api_local.post(
        f"{PREFIJO}/productos/DVU-PR49573/imagen",
        files=_foto("catalogo.pdf", "application/pdf"),
        headers=datos["auth_admin"],
    )

    assert r.status_code == 415


def test_el_vendedor_no_cambia_las_fotos(
    cliente_api_local: TestClient, datos: dict[str, Any]
) -> None:
    r = cliente_api_local.post(
        f"{PREFIJO}/productos/DVU-PR49573/imagen", files=_foto(), headers=datos["auth_vendedor"]
    )

    assert r.status_code == 403


def test_subir_la_foto_de_un_sku_que_no_existe(
    cliente_api_local: TestClient, datos: dict[str, Any]
) -> None:
    r = cliente_api_local.post(
        f"{PREFIJO}/productos/NO-EXISTE/imagen", files=_foto(), headers=datos["auth_admin"]
    )

    assert r.status_code == 404


def test_producto_sin_foto_responde_404_no_una_imagen_rota(
    cliente_api_local: TestClient, datos: dict[str, Any]
) -> None:
    r = cliente_api_local.get(f"{PREFIJO}/productos/DVU-PR49573/imagen", follow_redirects=False)

    assert r.status_code == 404
