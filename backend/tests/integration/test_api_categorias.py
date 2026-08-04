"""Árbol de categorías y su efecto sobre el catálogo.

Lo que se cuida acá es la regla que hace usable el árbol en producción: **la asignación
a mano manda sobre la clasificación automática**. Si `make clasificar` pisara lo que
corrigió el administrador, cada corrección duraría hasta la próxima corrida y nadie
volvería a corregir nada.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.carga.categorias import clasificar_catalogo, sembrar_categorias
from dvu.db.models import Categoria, Producto, Usuario
from dvu.seguridad import emitir_token, hashear

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"


def _producto(sku: str, descripcion: str) -> Producto:
    return Producto(
        sku=sku,
        descripcion=descripcion,
        multiplo_venta=12,
        unidad_venta="UNID",
        precio_lista_clp=Decimal("1790"),
    )


@pytest.fixture
def datos(sesion: Session) -> dict[str, Any]:
    admin = Usuario(email="a@test.cl", nombre="A", rol="admin", password_hash=hashear("x"))
    vendedor = Usuario(email="v@test.cl", nombre="V", rol="vendedor", password_hash=hashear("x"))
    sesion.add_all([admin, vendedor])
    sesion.add_all(
        [
            _producto("DVU-CODO", "CODO 90 PVC 110MM"),
            _producto("DVU-DISCO", "DISCO CORTE METAL 4 1/2"),
            _producto("DVU-RARO", "POLICARBONATO ALVEOLAR BRONCE"),
        ]
    )
    sesion.flush()

    return {
        "auth_admin": {"Authorization": f"Bearer {emitir_token(admin.uuid, 'admin')}"},
        "auth_vendedor": {"Authorization": f"Bearer {emitir_token(vendedor.uuid, 'vendedor')}"},
    }


def test_clasificar_arma_el_arbol_y_deja_fuera_lo_que_no_reconoce(
    sesion: Session, datos: dict[str, Any]
) -> None:
    resumen = clasificar_catalogo(sesion)

    assert resumen.clasificados == 2
    assert resumen.sin_categoria == 1
    assert "POLICARBONATO ALVEOLAR BRONCE" in resumen.ejemplos_sin_categoria

    sin_clasificar = sesion.scalar(select(Producto).where(Producto.sku == "DVU-RARO"))
    assert sin_clasificar is not None
    assert sin_clasificar.categoria_id is None


def test_sembrar_dos_veces_no_duplica_categorias(sesion: Session) -> None:
    sembrar_categorias(sesion)
    creadas_la_segunda_vez = sembrar_categorias(sesion)

    assert creadas_la_segunda_vez == 0
    slugs = [c.slug for c in sesion.scalars(select(Categoria))]
    assert len(slugs) == len(set(slugs))


def test_sembrar_no_pisa_el_nombre_que_puso_el_administrador(sesion: Session) -> None:
    """Si le cambió el nombre a una categoría, ese es el que usa la fuerza de venta."""
    sembrar_categorias(sesion)
    categoria = sesion.scalar(select(Categoria).where(Categoria.slug == "gasfiteria"))
    assert categoria is not None
    categoria.nombre = "Gasfitería y agua"
    sesion.flush()

    sembrar_categorias(sesion)

    assert categoria.nombre == "Gasfitería y agua"


def test_reclasificar_no_pisa_lo_que_asigno_una_persona(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    clasificar_catalogo(sesion)

    # El administrador decide que el policarbonato va en gasfitería, contra las reglas.
    respuesta = cliente_api.patch(
        f"{PREFIJO}/productos/DVU-RARO",
        json={"categoria_slug": "gasfiteria"},
        headers=datos["auth_admin"],
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["categoria_slug"] == "gasfiteria"

    resumen = clasificar_catalogo(sesion)

    assert resumen.clasificados == 0
    assert resumen.respetados == 3
    assert cliente_api.get(f"{PREFIJO}/productos/DVU-RARO").json()["categoria_slug"] == "gasfiteria"


def test_filtrar_el_catalogo_por_categoria(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    clasificar_catalogo(sesion)

    pagina = cliente_api.get(f"{PREFIJO}/productos", params={"categoria": "gasfiteria"}).json()

    assert pagina["total"] == 1
    assert pagina["items"][0]["sku"] == "DVU-CODO"
    assert pagina["items"][0]["categoria_nombre"] == "Gasfitería"


def test_una_categoria_inexistente_devuelve_vacio_no_404(
    sesion: Session, cliente_api: TestClient
) -> None:
    """El filtro llega desde un enlace o un marcador. Romper la página entera por un
    slug viejo no ayuda a nadie."""
    r = cliente_api.get(f"{PREFIJO}/productos", params={"categoria": "no-existe"})

    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_la_bandeja_de_sin_categoria_es_la_lista_de_revision(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    clasificar_catalogo(sesion)

    pagina = cliente_api.get(f"{PREFIJO}/productos", params={"sin_categoria": True}).json()

    assert [p["sku"] for p in pagina["items"]] == ["DVU-RARO"]


def test_las_categorias_vacias_no_se_ofrecen(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """Una categoría en el menú que no tiene productos es una promesa que el catálogo
    no cumple: el vendedor entra y no hay nada."""
    clasificar_catalogo(sesion)

    con_productos = {c["slug"] for c in cliente_api.get(f"{PREFIJO}/categorias").json()}
    todas = {
        c["slug"]
        for c in cliente_api.get(f"{PREFIJO}/categorias", params={"con_vacias": True}).json()
    }

    assert con_productos == {"gasfiteria", "abrasivos"}
    assert "pesca" in todas


def test_el_conteo_no_incluye_productos_desactivados(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    clasificar_catalogo(sesion)
    cliente_api.patch(
        f"{PREFIJO}/productos/DVU-CODO", json={"activo": False}, headers=datos["auth_admin"]
    )

    slugs = {c["slug"] for c in cliente_api.get(f"{PREFIJO}/categorias").json()}

    assert "gasfiteria" not in slugs


def test_asignar_una_categoria_que_no_existe_da_404(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    r = cliente_api.patch(
        f"{PREFIJO}/productos/DVU-CODO",
        json={"categoria_slug": "inventada"},
        headers=datos["auth_admin"],
    )

    assert r.status_code == 404


def test_sacarle_la_categoria_a_un_producto(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    clasificar_catalogo(sesion)

    r = cliente_api.patch(
        f"{PREFIJO}/productos/DVU-CODO",
        json={"categoria_slug": None},
        headers=datos["auth_admin"],
    )

    assert r.json()["categoria_slug"] is None


def test_el_vendedor_no_edita_el_arbol(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/categorias",
        json={"slug": "jardin", "nombre": "Jardín"},
        headers=datos["auth_vendedor"],
    )

    assert r.status_code == 403


def test_el_arbol_se_lee_sin_ingresar(sesion: Session, cliente_api: TestClient) -> None:
    assert cliente_api.get(f"{PREFIJO}/categorias").status_code == 200


def test_crear_una_categoria_repetida_da_409(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    sembrar_categorias(sesion)

    r = cliente_api.post(
        f"{PREFIJO}/categorias",
        json={"slug": "gasfiteria", "nombre": "Otra cosa"},
        headers=datos["auth_admin"],
    )

    assert r.status_code == 409


def test_renombrar_una_categoria(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    sembrar_categorias(sesion)

    r = cliente_api.patch(
        f"{PREFIJO}/categorias/pesca",
        json={"nombre": "Pesca deportiva"},
        headers=datos["auth_admin"],
    )

    assert r.status_code == 200
    assert r.json()["nombre"] == "Pesca deportiva"
