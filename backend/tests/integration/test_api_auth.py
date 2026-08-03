"""Autenticación de la API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from dvu.db.models import Usuario
from dvu.seguridad import emitir_token, hashear

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"


@pytest.fixture
def vendedor(sesion: Session) -> Usuario:
    usuario = Usuario(
        email="vendedor@test.cl",
        nombre="Vendedor Test",
        rol="vendedor",
        password_hash=hashear("clave-de-prueba"),
    )
    sesion.add(usuario)
    sesion.flush()
    return usuario


def test_login_devuelve_access_y_refresh(cliente_api: TestClient, vendedor: Usuario) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/auth/login",
        json={"email": "vendedor@test.cl", "password": "clave-de-prueba"},
    )

    assert r.status_code == 200
    datos = r.json()
    assert datos["token_type"] == "bearer"  # noqa: S105 — esquema, no un secreto
    assert datos["access_token"] and datos["refresh_token"]
    assert datos["expires_in"] > 0


def test_password_incorrecta_no_revela_si_el_email_existe(
    cliente_api: TestClient, vendedor: Usuario
) -> None:
    mala_clave = cliente_api.post(
        f"{PREFIJO}/auth/login", json={"email": "vendedor@test.cl", "password": "otra"}
    )
    no_existe = cliente_api.post(
        f"{PREFIJO}/auth/login", json={"email": "nadie@test.cl", "password": "otra"}
    )

    assert mala_clave.status_code == no_existe.status_code == 401
    assert mala_clave.json() == no_existe.json()


def test_usuario_desactivado_no_entra(
    cliente_api: TestClient, sesion: Session, vendedor: Usuario
) -> None:
    vendedor.activo = False
    sesion.flush()

    r = cliente_api.post(
        f"{PREFIJO}/auth/login",
        json={"email": "vendedor@test.cl", "password": "clave-de-prueba"},
    )
    assert r.status_code == 401


def test_yo_requiere_token(cliente_api: TestClient, vendedor: Usuario) -> None:
    assert cliente_api.get(f"{PREFIJO}/auth/yo").status_code == 401

    token = emitir_token(vendedor.uuid, vendedor.rol)
    r = cliente_api.get(f"{PREFIJO}/auth/yo", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    assert r.json()["email"] == "vendedor@test.cl"
    assert r.json()["rol"] == "vendedor"


def test_un_refresh_token_no_sirve_como_access(cliente_api: TestClient, vendedor: Usuario) -> None:
    refresh = emitir_token(vendedor.uuid, vendedor.rol, tipo="refresh")

    r = cliente_api.get(f"{PREFIJO}/auth/yo", headers={"Authorization": f"Bearer {refresh}"})
    assert r.status_code == 401


def test_refresh_renueva_el_access(cliente_api: TestClient, vendedor: Usuario) -> None:
    refresh = emitir_token(vendedor.uuid, vendedor.rol, tipo="refresh")

    r = cliente_api.post(f"{PREFIJO}/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200

    nuevo = r.json()["access_token"]
    yo = cliente_api.get(f"{PREFIJO}/auth/yo", headers={"Authorization": f"Bearer {nuevo}"})
    assert yo.status_code == 200


def test_token_adulterado_se_rechaza(cliente_api: TestClient, vendedor: Usuario) -> None:
    token = emitir_token(vendedor.uuid, vendedor.rol)
    adulterado = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")

    r = cliente_api.get(f"{PREFIJO}/auth/yo", headers={"Authorization": f"Bearer {adulterado}"})
    assert r.status_code == 401
