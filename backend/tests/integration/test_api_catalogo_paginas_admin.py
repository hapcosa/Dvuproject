"""El administrador arma la maqueta: agrega portadas y ofertas, y saca las vencidas.

Lo que hay detrás de cada página son dos objetos —el PDF que se reinserta al exportar y
el PNG que ve la web—. Que se guarden los dos es la parte que no se puede romper: un
`key_pdf` inválido no falla, `catalogo-pdf` saltea la página y la portada desaparece del
catálogo sin aviso.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any, BinaryIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from dvu.almacenamiento import get_almacen
from dvu.db.models import CatalogoPagina, Usuario
from dvu.seguridad import emitir_token, hashear

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"


class AlmacenDeMentira:
    def __init__(self) -> None:
        self.contenido: dict[str, bytes] = {}

    def guardar(self, key: str, datos: BinaryIO, content_type: str) -> str:
        self.contenido[key] = datos.read()
        return key

    def url_firmada(self, key: str, *, segundos: int = 300) -> str:
        return f"https://almacen.ejemplo/{key}"

    def leer(self, key: str) -> bytes | None:
        return self.contenido.get(key)


@pytest.fixture
def almacen(cliente_api: TestClient) -> Iterator[AlmacenDeMentira]:
    alm = AlmacenDeMentira()
    cliente_api.app.dependency_overrides[get_almacen] = lambda: alm
    yield alm
    cliente_api.app.dependency_overrides.pop(get_almacen, None)


@pytest.fixture
def datos(sesion: Session) -> dict[str, Any]:
    admin = Usuario(email="a@test.cl", nombre="A", rol="admin", password_hash=hashear("x"))
    vendedor = Usuario(email="v@test.cl", nombre="V", rol="vendedor", password_hash=hashear("x"))
    sesion.add_all([admin, vendedor])
    sesion.flush()
    return {
        "auth_admin": {"Authorization": f"Bearer {emitir_token(admin.uuid, 'admin')}"},
        "auth_vendedor": {"Authorization": f"Bearer {emitir_token(vendedor.uuid, 'vendedor')}"},
    }


def _pdf_de_una_pagina() -> bytes:
    import fitz

    documento = fitz.open()
    documento.new_page(width=595, height=842)
    return bytes(documento.tobytes())


def _png() -> bytes:
    import fitz

    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 80))
    pix.clear_with(255)
    return bytes(pix.tobytes("png"))


def _subir(
    cliente: TestClient, datos: dict[str, Any], contenido: bytes, nombre: str, tipo: str, cual: str
) -> Any:
    return cliente.post(
        f"{PREFIJO}/catalogo/paginas",
        files={"archivo": (nombre, io.BytesIO(contenido), tipo)},
        data={"tipo": cual},
        headers=datos["auth_admin"],
    )


def test_agregar_una_portada_en_pdf_guarda_las_dos_mitades(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any], sesion: Session
) -> None:
    r = _subir(
        cliente_api, datos, _pdf_de_una_pagina(), "portada.pdf", "application/pdf", "portada"
    )

    assert r.status_code == 201
    assert r.json()["tipo"] == "portada"

    registro = sesion.get(CatalogoPagina, r.json()["id"])
    assert registro is not None
    assert almacen.contenido[registro.key_pdf].startswith(b"%PDF")
    assert almacen.contenido[registro.key_png].startswith(b"\x89PNG")


def test_subir_una_imagen_igual_deja_un_pdf_reinsertable(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any], sesion: Session
) -> None:
    """El diseñador manda un JPG y el catálogo exportado tiene que seguir armándose."""
    r = _subir(cliente_api, datos, _png(), "oferta.png", "image/png", "promocion")

    assert r.status_code == 201
    registro = sesion.get(CatalogoPagina, r.json()["id"])
    assert registro is not None
    assert almacen.contenido[registro.key_pdf].startswith(b"%PDF")


def test_la_pagina_agregada_sale_en_el_catalogo(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    creada = _subir(cliente_api, datos, _png(), "p.png", "image/png", "contraportada").json()

    paginas = cliente_api.get(f"{PREFIJO}/catalogo/paginas").json()

    assert creada["id"] in [p["id"] for p in paginas]


def test_quitar_una_pagina_la_saca_del_catalogo_sin_borrarla(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any], sesion: Session
) -> None:
    """Una oferta que sale en agosto suele volver, y un PDF viejo sigue apuntando a ella."""
    creada = _subir(cliente_api, datos, _png(), "p.png", "image/png", "promocion").json()

    r = cliente_api.delete(
        f"{PREFIJO}/catalogo/paginas/{creada['id']}", headers=datos["auth_admin"]
    )

    assert r.status_code == 200
    assert r.json()["activa"] is False
    publicas = cliente_api.get(f"{PREFIJO}/catalogo/paginas").json()
    assert creada["id"] not in [p["id"] for p in publicas]
    # El registro y el objeto siguen ahí: es lo que permite reponerla.
    registro = sesion.get(CatalogoPagina, creada["id"])
    assert registro is not None
    assert registro.key_png in almacen.contenido


def test_una_pagina_quitada_se_repone(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    creada = _subir(cliente_api, datos, _png(), "p.png", "image/png", "promocion").json()
    cliente_api.delete(f"{PREFIJO}/catalogo/paginas/{creada['id']}", headers=datos["auth_admin"])

    r = cliente_api.patch(
        f"{PREFIJO}/catalogo/paginas/{creada['id']}",
        json={"activa": True},
        headers=datos["auth_admin"],
    )

    assert r.status_code == 200
    publicas = cliente_api.get(f"{PREFIJO}/catalogo/paginas").json()
    assert creada["id"] in [p["id"] for p in publicas]


def test_la_administracion_ve_las_quitadas_y_el_catalogo_no(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """Sin verlas no habría cómo reponerlas desde la pantalla."""
    creada = _subir(cliente_api, datos, _png(), "p.png", "image/png", "promocion").json()
    cliente_api.delete(f"{PREFIJO}/catalogo/paginas/{creada['id']}", headers=datos["auth_admin"])

    todas = cliente_api.get(f"{PREFIJO}/catalogo/paginas?incluir_inactivas=true").json()

    assert creada["id"] in [p["id"] for p in todas]


def test_cambiar_donde_va_la_pagina(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    creada = _subir(cliente_api, datos, _png(), "p.png", "image/png", "promocion").json()

    r = cliente_api.patch(
        f"{PREFIJO}/catalogo/paginas/{creada['id']}",
        json={"tipo": "contraportada"},
        headers=datos["auth_admin"],
    )

    assert r.status_code == 200
    assert r.json()["tipo"] == "contraportada"


def test_un_tipo_inventado_no_pasa(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """La tabla tiene la misma restricción; el 422 evita que reviente como error de base."""
    r = _subir(cliente_api, datos, _png(), "p.png", "image/png", "medio")

    assert r.status_code == 422


def test_un_archivo_que_no_es_pagina_se_rechaza(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    r = _subir(cliente_api, datos, b"hola", "notas.txt", "text/plain", "portada")

    assert r.status_code == 415


def test_un_pdf_corrupto_avisa_en_vez_de_romper(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    r = _subir(cliente_api, datos, b"%PDF pero no", "roto.pdf", "application/pdf", "portada")

    assert r.status_code == 422


def test_el_vendedor_no_toca_la_maqueta(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/catalogo/paginas",
        files={"archivo": ("p.png", io.BytesIO(_png()), "image/png")},
        data={"tipo": "portada"},
        headers=datos["auth_vendedor"],
    )

    assert r.status_code == 403


def test_quitar_una_pagina_que_no_existe(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    r = cliente_api.delete(f"{PREFIJO}/catalogo/paginas/9999", headers=datos["auth_admin"])

    assert r.status_code == 404


def test_dos_paginas_seguidas_no_chocan_de_posicion(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """(archivo, página) es único: sin numeración automática la segunda subida fallaría."""
    primera = _subir(cliente_api, datos, _png(), "a.png", "image/png", "promocion")
    segunda = _subir(cliente_api, datos, _png(), "b.png", "image/png", "promocion")

    assert primera.status_code == 201
    assert segunda.status_code == 201
    assert primera.json()["pagina"] != segunda.json()["pagina"]


# --- orden dentro de la sección ---------------------------------------------
#
# El orden entre secciones no se prueba acá porque no es configurable: lo fija el tipo
# (`dvu.db.maqueta`). Lo que sigue es lo que el administrador sí mueve.


def _ids(cliente: TestClient, tipo: str | None = None) -> list[int]:
    paginas = cliente.get(f"{PREFIJO}/catalogo/paginas?incluir_inactivas=true").json()
    return [p["id"] for p in paginas if tipo is None or p["tipo"] == tipo]


def test_las_paginas_nuevas_se_apilan_al_final_de_su_seccion(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    primera = _subir(cliente_api, datos, _png(), "a.png", "image/png", "promocion").json()
    segunda = _subir(cliente_api, datos, _png(), "b.png", "image/png", "promocion").json()

    assert segunda["orden"] > primera["orden"]
    assert _ids(cliente_api, "promocion") == [primera["id"], segunda["id"]]


def test_arrastrar_reordena_la_seccion(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    primera = _subir(cliente_api, datos, _png(), "a.png", "image/png", "portada").json()
    segunda = _subir(cliente_api, datos, _png(), "b.png", "image/png", "portada").json()

    r = cliente_api.put(
        f"{PREFIJO}/catalogo/paginas/orden",
        json={"ids": [segunda["id"], primera["id"]]},
        headers=datos["auth_admin"],
    )

    assert r.status_code == 200
    assert _ids(cliente_api, "portada") == [segunda["id"], primera["id"]]


def test_el_orden_se_reescribe_entero_y_no_deja_huecos(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """Es lo que permite mandar la lista completa en cada arrastre sin acumular saltos."""
    creadas = [
        _subir(cliente_api, datos, _png(), f"{i}.png", "image/png", "promocion").json()
        for i in range(3)
    ]

    cliente_api.put(
        f"{PREFIJO}/catalogo/paginas/orden",
        json={"ids": [c["id"] for c in reversed(creadas)]},
        headers=datos["auth_admin"],
    )

    paginas = cliente_api.get(f"{PREFIJO}/catalogo/paginas").json()
    assert [p["orden"] for p in paginas] == [1, 2, 3]


def test_mover_una_pagina_de_seccion_la_manda_al_final_de_la_nueva(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """Su posición anterior era de la otra sección y ahí no significa nada."""
    quieta = _subir(cliente_api, datos, _png(), "a.png", "image/png", "contraportada").json()
    viajera = _subir(cliente_api, datos, _png(), "b.png", "image/png", "promocion").json()

    r = cliente_api.patch(
        f"{PREFIJO}/catalogo/paginas/{viajera['id']}",
        json={"tipo": "contraportada"},
        headers=datos["auth_admin"],
    )

    assert r.status_code == 200
    assert r.json()["orden"] > quieta["orden"]
    assert _ids(cliente_api, "contraportada") == [quieta["id"], viajera["id"]]


def test_reordenar_ignora_los_ids_que_no_existen(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """Pasa cuando otra pestaña borró una página: se guarda el resto en vez de fallar."""
    creada = _subir(cliente_api, datos, _png(), "a.png", "image/png", "portada").json()

    r = cliente_api.put(
        f"{PREFIJO}/catalogo/paginas/orden",
        json={"ids": [9999, creada["id"]]},
        headers=datos["auth_admin"],
    )

    assert r.status_code == 200
    assert [p["id"] for p in r.json()] == [creada["id"]]


def test_el_vendedor_no_reordena_la_maqueta(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    r = cliente_api.put(
        f"{PREFIJO}/catalogo/paginas/orden", json={"ids": []}, headers=datos["auth_vendedor"]
    )

    assert r.status_code == 403


# --- reemplazar el archivo de una página ------------------------------------


def _reemplazar(
    cliente: TestClient, datos: dict[str, Any], pagina_id: int, contenido: bytes, tipo: str
) -> Any:
    return cliente.put(
        f"{PREFIJO}/catalogo/paginas/{pagina_id}/archivo",
        files={"archivo": ("nueva.png", io.BytesIO(contenido), tipo)},
        headers=datos["auth_admin"],
    )


def test_reemplazar_el_archivo_conserva_el_lugar(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any], sesion: Session
) -> None:
    """Es el caso del diseñador que manda la portada corregida: no hay que reacomodarla."""
    primera = _subir(cliente_api, datos, _png(), "a.png", "image/png", "portada").json()
    segunda = _subir(cliente_api, datos, _png(), "b.png", "image/png", "portada").json()
    antes = sesion.get(CatalogoPagina, primera["id"])
    assert antes is not None
    key_vieja = antes.key_png

    r = _reemplazar(cliente_api, datos, primera["id"], _pdf_de_una_pagina(), "application/pdf")

    assert r.status_code == 200
    assert r.json()["orden"] == primera["orden"]
    assert _ids(cliente_api, "portada") == [primera["id"], segunda["id"]]
    sesion.refresh(antes)
    assert antes.key_png != key_vieja
    assert almacen.contenido[antes.key_pdf].startswith(b"%PDF")
    # El objeto viejo no se borra: el PDF exportado ayer todavía lo referencia.
    assert key_vieja in almacen.contenido


def test_reemplazar_con_un_archivo_ilegible_no_toca_la_pagina(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any], sesion: Session
) -> None:
    creada = _subir(cliente_api, datos, _png(), "a.png", "image/png", "portada").json()
    registro = sesion.get(CatalogoPagina, creada["id"])
    assert registro is not None
    key_buena = registro.key_png

    r = _reemplazar(cliente_api, datos, creada["id"], b"%PDF pero no", "application/pdf")

    assert r.status_code == 422
    sesion.refresh(registro)
    assert registro.key_png == key_buena


def test_reemplazar_una_pagina_que_no_existe(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    r = _reemplazar(cliente_api, datos, 9999, _png(), "image/png")

    assert r.status_code == 404


def test_el_vendedor_no_reemplaza_el_arte(
    cliente_api: TestClient, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    creada = _subir(cliente_api, datos, _png(), "a.png", "image/png", "portada").json()

    r = cliente_api.put(
        f"{PREFIJO}/catalogo/paginas/{creada['id']}/archivo",
        files={"archivo": ("n.png", io.BytesIO(_png()), "image/png")},
        headers=datos["auth_vendedor"],
    )

    assert r.status_code == 403
