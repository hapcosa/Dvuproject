"""Alta y baja de usuarios por la administración.

Antes un vendedor nuevo se creaba desde la consola de la base: dar de alta a alguien era
tarea de quien opera el repo, no del dueño del negocio.

Lo que se cuida acá es el reparto de accesos: quién puede repartirlos, qué roles se
pueden dar, y que nadie se deje a sí mismo fuera con un clic.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.db.models import Usuario
from dvu.domain.roles import ADMIN, EDITOR, VENDEDOR
from dvu.seguridad import emitir_token, hashear, verificar

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"

#: Contraseña de prueba. Larga porque el endpoint exige un piso, y con nombre porque
#: repetirla literal en cada test hace que ruff la lea como un secreto filtrado.
CLAVE = "clave-larga-1234"
CLAVE_NUEVA = "otra-clave-larga"


def _auth(usuario: Usuario) -> dict[str, str]:
    return {"Authorization": f"Bearer {emitir_token(usuario.uuid, usuario.rol)}"}


@pytest.fixture
def gente(sesion: Session) -> dict[str, Any]:
    admin = Usuario(email="a@test.cl", nombre="Admin", rol=ADMIN, password_hash=hashear("x"))
    editor = Usuario(email="e@test.cl", nombre="Editor", rol=EDITOR, password_hash=hashear("x"))
    vendedor = Usuario(email="v@test.cl", nombre="Vend", rol=VENDEDOR, password_hash=hashear("x"))
    sesion.add_all([admin, editor, vendedor])
    sesion.flush()
    return {
        "admin": admin,
        "editor": editor,
        "vendedor": vendedor,
        "auth_admin": _auth(admin),
        "auth_editor": _auth(editor),
        "auth_vendedor": _auth(vendedor),
    }


def _nuevo(**extra: Any) -> dict[str, Any]:
    datos = {
        "email": "nueva@dvu.cl",
        "nombre": "María Pérez",
        "rol": VENDEDOR,
        "password": CLAVE,
    }
    datos.update(extra)
    return datos


def test_el_administrador_crea_un_vendedor(
    cliente_api: TestClient, gente: dict[str, Any], sesion: Session
) -> None:
    r = cliente_api.post(f"{PREFIJO}/usuarios", json=_nuevo(), headers=gente["auth_admin"])

    assert r.status_code == 201
    assert r.json()["rol"] == VENDEDOR
    creada = sesion.scalar(select(Usuario).where(Usuario.email == "nueva@dvu.cl"))
    assert creada is not None
    # La contraseña se guarda hasheada, nunca como la escribió quien la creó.
    assert creada.password_hash != CLAVE
    assert verificar(CLAVE, creada.password_hash)


def test_el_administrador_crea_un_editor(cliente_api: TestClient, gente: dict[str, Any]) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/usuarios", json=_nuevo(rol=EDITOR), headers=gente["auth_admin"]
    )

    assert r.status_code == 201
    assert r.json()["rol"] == EDITOR


def test_el_correo_se_guarda_normalizado(cliente_api: TestClient, gente: dict[str, Any]) -> None:
    """Se entra escribiendo el correo, y nadie recuerda con qué mayúsculas lo dieron de
    alta. El login compara la cadena tal cual, así que la normalización es lo que evita
    un «credenciales inválidas» sin explicación."""
    r = cliente_api.post(
        f"{PREFIJO}/usuarios", json=_nuevo(email="Maria.Perez@DVU.CL"), headers=gente["auth_admin"]
    )

    assert r.json()["email"] == "maria.perez@dvu.cl"


def test_no_se_repite_el_correo(cliente_api: TestClient, gente: dict[str, Any]) -> None:
    cliente_api.post(f"{PREFIJO}/usuarios", json=_nuevo(), headers=gente["auth_admin"])

    r = cliente_api.post(f"{PREFIJO}/usuarios", json=_nuevo(), headers=gente["auth_admin"])

    assert r.status_code == 409
    assert "nueva@dvu.cl" in r.json()["detail"]


@pytest.mark.parametrize("rol", ["admin", "bodega", "cliente", "inventado"])
def test_solo_se_pueden_dar_los_roles_asignables(
    cliente_api: TestClient, gente: dict[str, Any], rol: str
) -> None:
    """`admin` queda fuera a propósito: repartir el rol que puede todo no debería ser un
    formulario más de la pantalla. `cliente` ya no existe —este sistema es sólo para
    gente que trabaja en DVU— y el endpoint tiene que decirlo, no aceptarlo callado."""
    r = cliente_api.post(f"{PREFIJO}/usuarios", json=_nuevo(rol=rol), headers=gente["auth_admin"])

    assert r.status_code == 422


def test_la_contrasena_tiene_un_piso(cliente_api: TestClient, gente: dict[str, Any]) -> None:
    """Estas cuentas entran a los precios y a la cartera desde cualquier navegador."""
    corta = "corta"
    r = cliente_api.post(
        f"{PREFIJO}/usuarios", json=_nuevo(password=corta), headers=gente["auth_admin"]
    )

    assert r.status_code == 422


@pytest.mark.parametrize("quien", ["auth_editor", "auth_vendedor"])
def test_solo_la_administracion_reparte_accesos(
    cliente_api: TestClient, gente: dict[str, Any], quien: str
) -> None:
    """Un editor mantiene el catálogo; repartir accesos es otra cosa."""
    assert cliente_api.get(f"{PREFIJO}/usuarios", headers=gente[quien]).status_code == 403
    assert (
        cliente_api.post(f"{PREFIJO}/usuarios", json=_nuevo(), headers=gente[quien]).status_code
        == 403
    )


def test_los_inactivos_no_salen_salvo_que_se_pidan(
    cliente_api: TestClient, gente: dict[str, Any], sesion: Session
) -> None:
    gente["vendedor"].activo = False
    sesion.flush()

    activos = cliente_api.get(f"{PREFIJO}/usuarios", headers=gente["auth_admin"]).json()
    todos = cliente_api.get(
        f"{PREFIJO}/usuarios?incluir_inactivos=true", headers=gente["auth_admin"]
    ).json()

    assert "v@test.cl" not in [u["email"] for u in activos]
    assert "v@test.cl" in [u["email"] for u in todos]


def test_desactivar_deja_a_la_persona_fuera_sin_borrarla(
    cliente_api: TestClient, gente: dict[str, Any], sesion: Session
) -> None:
    """Un usuario no se borra: sus pedidos y comprobantes lo apuntan, y borrar la fila
    dejaría documentos sin autor."""
    uuid = gente["vendedor"].uuid

    r = cliente_api.patch(
        f"{PREFIJO}/usuarios/{uuid}", json={"activo": False}, headers=gente["auth_admin"]
    )

    assert r.status_code == 200
    assert r.json()["activo"] is False
    assert sesion.scalar(select(Usuario).where(Usuario.uuid == uuid)) is not None
    # Y deja de poder entrar de inmediato, sin esperar a que venza su token.
    assert cliente_api.get(f"{PREFIJO}/auth/yo", headers=gente["auth_vendedor"]).status_code == 401


def test_nadie_se_desactiva_a_si_mismo(cliente_api: TestClient, gente: dict[str, Any]) -> None:
    """Con un solo administrador no habría quién lo deshaga desde la web."""
    r = cliente_api.patch(
        f"{PREFIJO}/usuarios/{gente['admin'].uuid}",
        json={"activo": False},
        headers=gente["auth_admin"],
    )

    assert r.status_code == 409


def test_el_rol_de_un_administrador_no_se_cambia_desde_la_web(
    cliente_api: TestClient, gente: dict[str, Any]
) -> None:
    """`ASIGNABLES` no incluye `admin`, así que sería un viaje de ida."""
    r = cliente_api.patch(
        f"{PREFIJO}/usuarios/{gente['admin'].uuid}",
        json={"rol": VENDEDOR},
        headers=gente["auth_admin"],
    )

    assert r.status_code == 409


def test_cambiar_la_contrasena_deja_entrar_con_la_nueva(
    cliente_api: TestClient, gente: dict[str, Any], sesion: Session
) -> None:
    cliente_api.patch(
        f"{PREFIJO}/usuarios/{gente['vendedor'].uuid}",
        json={"password": CLAVE_NUEVA},
        headers=gente["auth_admin"],
    )
    sesion.refresh(gente["vendedor"])

    assert verificar(CLAVE_NUEVA, gente["vendedor"].password_hash)


def test_los_roles_asignables_vienen_con_su_descripcion(
    cliente_api: TestClient, gente: dict[str, Any]
) -> None:
    """La pantalla no reescribe qué hace cada rol: sería una segunda copia."""
    r = cliente_api.get(f"{PREFIJO}/usuarios/roles", headers=gente["auth_admin"])

    codigos = {x["codigo"] for x in r.json()}
    assert codigos == {VENDEDOR, EDITOR}
    assert all(x["descripcion"] for x in r.json())
