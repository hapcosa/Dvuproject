"""La maqueta del impreso servida por la API: banda, páginas de arte y logo de marca.

Sin esto la web es una tabla cualquiera. Con esto es la hoja que el ferretero ya conoce,
que es lo que hace que no haya que explicarle nada.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any, BinaryIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from dvu.almacenamiento import get_almacen
from dvu.db.models import CatalogoActivo, CatalogoPagina, Producto

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"


class AlmacenDeMentira:
    def __init__(self) -> None:
        self.contenido: dict[str, bytes] = {}

    def guardar(self, key: str, datos: BinaryIO, content_type: str) -> str:
        self.contenido[key] = datos.read()
        return key

    def url_firmada(self, key: str, *, segundos: int = 300) -> str:
        return f"https://almacen.ejemplo/{key}?firma=abc"

    def leer(self, key: str) -> bytes | None:
        return self.contenido.get(key)


@pytest.fixture
def almacen(cliente_api: TestClient) -> Iterator[AlmacenDeMentira]:
    """El almacén de verdad no se toca: lo que se prueba es que se firme la URL, no S3."""
    alm = AlmacenDeMentira()
    cliente_api.app.dependency_overrides[get_almacen] = lambda: alm
    yield alm
    cliente_api.app.dependency_overrides.pop(get_almacen, None)


@pytest.fixture
def plantilla(sesion: Session, almacen: AlmacenDeMentira) -> dict[str, Any]:
    # El contenido va al almacén además de la key a la base: la API sirve los bytes, así
    # que una fila sin objeto detrás es un 404 y no una imagen.
    almacen.contenido.update(
        {
            "catalogo/plantilla/impar.png": b"\x89PNG banda impar",
            "catalogo/paginas/p1.png": b"\x89PNG portada",
            "catalogo/marcas/vinilit.png": b"\x89PNG logo vinilit",
        }
    )
    sesion.add(CatalogoActivo(clave="banner_impar", key_objeto="catalogo/plantilla/impar.png"))
    portada = CatalogoPagina(
        archivo="CAT PARTE 1.pdf",
        pagina=1,
        tipo="portada",
        key_pdf="catalogo/paginas/p1.pdf",
        key_png="catalogo/paginas/p1.png",
    )
    oculta = CatalogoPagina(
        archivo="CAT PARTE 1.pdf",
        pagina=9,
        tipo="promocion",
        key_pdf="catalogo/paginas/p9.pdf",
        key_png="catalogo/paginas/p9.png",
        activa=False,
    )
    producto = Producto(
        sku="DVU-CODO",
        descripcion="CODO 90 PVC 110MM",
        multiplo_venta=12,
        unidad_venta="UNID",
        precio_lista_clp=Decimal("1790"),
        marca_logo_key="catalogo/marcas/vinilit.png",
    )
    sin_logo = Producto(
        sku="DVU-MANUAL",
        descripcion="PRODUCTO CARGADO A MANO",
        multiplo_venta=1,
        unidad_venta="UNID",
        precio_lista_clp=Decimal("500"),
    )
    sesion.add_all([portada, oculta, producto, sin_logo])
    sesion.flush()
    return {"portada_id": portada.id, "oculta_id": oculta.id}


def test_la_banda_del_impreso_se_sirve_por_paridad(
    cliente_api: TestClient, almacen: AlmacenDeMentira, plantilla: dict[str, Any]
) -> None:
    respuesta = cliente_api.get(f"{PREFIJO}/catalogo/banner/impar", follow_redirects=False)

    assert respuesta.status_code == 200
    assert respuesta.content == b"\x89PNG banda impar"


def test_la_banda_que_no_esta_cargada_da_404_y_no_rompe_la_pagina(
    cliente_api: TestClient, almacen: AlmacenDeMentira, plantilla: dict[str, Any]
) -> None:
    """Antes de la primera extracción de plantilla no hay banda. La web dibuja la suya en
    CSS; lo que no puede es quedarse esperando un 500."""
    assert cliente_api.get(f"{PREFIJO}/catalogo/banner/par").status_code == 404


def test_una_paridad_inventada_no_existe(
    cliente_api: TestClient, almacen: AlmacenDeMentira, plantilla: dict[str, Any]
) -> None:
    assert cliente_api.get(f"{PREFIJO}/catalogo/banner/../secreto").status_code == 404


def test_lista_las_paginas_de_arte_activas(
    cliente_api: TestClient, almacen: AlmacenDeMentira, plantilla: dict[str, Any]
) -> None:
    """Una oferta vencida se desactiva, no se borra: el catálogo del año pasado sigue
    siendo la referencia de un pedido viejo."""
    paginas = cliente_api.get(f"{PREFIJO}/catalogo/paginas").json()

    assert [(p["pagina"], p["tipo"]) for p in paginas] == [(1, "portada")]


def test_la_vista_previa_de_la_pagina_la_sirve_la_api(
    cliente_api: TestClient, almacen: AlmacenDeMentira, plantilla: dict[str, Any]
) -> None:
    """El bucket no se abre al público, ni siquiera para la portada: el contenido sale
    por la API. Una URL firmada apuntaría al endpoint interno de MinIO, que desde la LAN
    o la VPN no resuelve."""
    respuesta = cliente_api.get(
        f"{PREFIJO}/catalogo/paginas/{plantilla['portada_id']}/imagen", follow_redirects=False
    )

    assert respuesta.status_code == 200
    assert respuesta.content == b"\x89PNG portada"
    assert respuesta.headers["content-type"] == "image/png"


def test_la_pagina_sin_objeto_en_el_bucket_da_404(
    cliente_api: TestClient, almacen: AlmacenDeMentira, plantilla: dict[str, Any]
) -> None:
    """La fila puede existir sin el objeto si la carga quedó a medias. Mejor un 404 que
    un 500 en medio del catálogo."""
    almacen.contenido.pop("catalogo/paginas/p1.png")

    respuesta = cliente_api.get(f"{PREFIJO}/catalogo/paginas/{plantilla['portada_id']}/imagen")

    assert respuesta.status_code == 404


def test_el_catalogo_es_publico_sin_ingresar(
    cliente_api: TestClient, almacen: AlmacenDeMentira, plantilla: dict[str, Any]
) -> None:
    """El catálogo es la vidriera: pedir sesión para verlo mata la venta."""
    assert cliente_api.get(f"{PREFIJO}/catalogo/paginas").status_code == 200


def test_el_logo_de_marca_se_sirve_desde_el_producto(
    cliente_api: TestClient, almacen: AlmacenDeMentira, plantilla: dict[str, Any]
) -> None:
    respuesta = cliente_api.get(f"{PREFIJO}/productos/DVU-CODO/marca", follow_redirects=False)

    assert respuesta.status_code == 200
    assert respuesta.content == b"\x89PNG logo vinilit"


def test_el_producto_sin_logo_no_finge_tener_uno(
    cliente_api: TestClient, almacen: AlmacenDeMentira, plantilla: dict[str, Any]
) -> None:
    """Los productos que carga el administrador no vienen del PDF y no tienen logo. La
    web escribe el nombre de la marca en vez de mostrar una imagen rota."""
    assert cliente_api.get(f"{PREFIJO}/productos/DVU-MANUAL/marca").status_code == 404


def test_el_listado_dice_cuales_productos_traen_logo(
    cliente_api: TestClient, almacen: AlmacenDeMentira, plantilla: dict[str, Any]
) -> None:
    """La web decide con este campo si pinta el logo o el nombre, sin pedir una imagen
    por producto para descubrir que no existe."""
    items = cliente_api.get(f"{PREFIJO}/productos?limite=50").json()["items"]
    por_sku = {p["sku"]: p for p in items}

    assert por_sku["DVU-CODO"]["marca_logo_key"] == "catalogo/marcas/vinilit.png"
    assert por_sku["DVU-MANUAL"]["marca_logo_key"] is None
