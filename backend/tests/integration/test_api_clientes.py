"""Alta y mantención de ferreterías."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.db.models import Cliente, Usuario
from dvu.seguridad import emitir_token, hashear

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"


@pytest.fixture
def usuarios(sesion: Session) -> dict[str, Any]:
    uno = Usuario(
        email="v1@test.cl", nombre="Vendedor Uno", rol="vendedor", password_hash=hashear("x")
    )
    dos = Usuario(
        email="v2@test.cl", nombre="Vendedor Dos", rol="vendedor", password_hash=hashear("x")
    )
    admin = Usuario(email="a@test.cl", nombre="Admin", rol="admin", password_hash=hashear("x"))
    sesion.add_all([uno, dos, admin])
    sesion.flush()

    return {
        "uno": uno,
        "dos": dos,
        "auth_uno": {"Authorization": f"Bearer {emitir_token(uno.uuid, 'vendedor')}"},
        "auth_dos": {"Authorization": f"Bearer {emitir_token(dos.uuid, 'vendedor')}"},
        "auth_admin": {"Authorization": f"Bearer {emitir_token(admin.uuid, 'admin')}"},
    }


def _payload(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "rut": "76.123.456-0",
        "razon_social": "FERRETERIA TEST SPA",
        "giro": "VENTA AL POR MENOR DE ARTICULOS DE FERRETERIA",
        "comuna": "Puente Alto",
        "email_dte": "facturas@ferreteriatest.cl",
        "condicion_pago": "credito_30",
    }
    base.update(extra)
    return base


def test_alta_normaliza_el_rut(cliente_api: TestClient, usuarios: dict[str, Any]) -> None:
    """Entra con puntos, se guarda canónico: así lo espera el DTE."""
    r = cliente_api.post(f"{PREFIJO}/clientes", json=_payload(), headers=usuarios["auth_uno"])

    assert r.status_code == 201
    assert r.json()["rut"] == "76123456-0"
    assert r.json()["activo"] is True


def test_alta_rechaza_un_rut_con_digito_verificador_malo(
    cliente_api: TestClient, usuarios: dict[str, Any]
) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/clientes", json=_payload(rut="76123456-2"), headers=usuarios["auth_uno"]
    )
    assert r.status_code == 422


def test_alta_rechaza_una_condicion_de_pago_inventada(
    cliente_api: TestClient, usuarios: dict[str, Any]
) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/clientes",
        json=_payload(condicion_pago="a_90_dias"),
        headers=usuarios["auth_uno"],
    )
    assert r.status_code == 422


def test_rut_repetido_es_conflicto(cliente_api: TestClient, usuarios: dict[str, Any]) -> None:
    cliente_api.post(f"{PREFIJO}/clientes", json=_payload(), headers=usuarios["auth_uno"])
    r = cliente_api.post(
        f"{PREFIJO}/clientes",
        json=_payload(razon_social="OTRO NOMBRE SPA"),
        headers=usuarios["auth_uno"],
    )
    assert r.status_code == 409


def test_el_vendedor_solo_ve_su_cartera(cliente_api: TestClient, usuarios: dict[str, Any]) -> None:
    cliente_api.post(f"{PREFIJO}/clientes", json=_payload(), headers=usuarios["auth_uno"])
    cliente_api.post(
        f"{PREFIJO}/clientes",
        json=_payload(rut="77987654-3", razon_social="OTRA FERRETERIA LTDA"),
        headers=usuarios["auth_dos"],
    )

    mios = cliente_api.get(f"{PREFIJO}/clientes", headers=usuarios["auth_uno"]).json()
    assert [c["rut"] for c in mios["items"]] == ["76123456-0"]

    todos = cliente_api.get(f"{PREFIJO}/clientes", headers=usuarios["auth_admin"]).json()
    assert todos["total"] == 2


def test_el_detalle_de_un_cliente_ajeno_se_niega(
    cliente_api: TestClient, usuarios: dict[str, Any]
) -> None:
    cliente_api.post(f"{PREFIJO}/clientes", json=_payload(), headers=usuarios["auth_uno"])

    ajeno = cliente_api.get(f"{PREFIJO}/clientes/76123456-0", headers=usuarios["auth_dos"])
    assert ajeno.status_code == 403

    propio = cliente_api.get(f"{PREFIJO}/clientes/76.123.456-0", headers=usuarios["auth_uno"])
    assert propio.status_code == 200


def test_parche_solo_toca_lo_enviado(cliente_api: TestClient, usuarios: dict[str, Any]) -> None:
    cliente_api.post(f"{PREFIJO}/clientes", json=_payload(), headers=usuarios["auth_uno"])

    r = cliente_api.patch(
        f"{PREFIJO}/clientes/76123456-0",
        json={"telefono": "+56 9 1234 5678"},
        headers=usuarios["auth_uno"],
    )

    assert r.status_code == 200
    assert r.json()["telefono"] == "+56 9 1234 5678"
    assert r.json()["condicion_pago"] == "credito_30"  # intacto


def test_desactivar_no_borra_al_cliente(
    cliente_api: TestClient, sesion: Session, usuarios: dict[str, Any]
) -> None:
    """Tiene pedidos y pagos colgando: se saca de circulación, no se borra."""
    cliente_api.post(f"{PREFIJO}/clientes", json=_payload(), headers=usuarios["auth_uno"])

    r = cliente_api.patch(
        f"{PREFIJO}/clientes/76123456-0", json={"activo": False}, headers=usuarios["auth_uno"]
    )
    assert r.status_code == 200

    listado = cliente_api.get(f"{PREFIJO}/clientes", headers=usuarios["auth_uno"]).json()
    assert listado["total"] == 0

    con_inactivos = cliente_api.get(
        f"{PREFIJO}/clientes?incluir_inactivos=true", headers=usuarios["auth_uno"]
    ).json()
    assert con_inactivos["total"] == 1

    assert sesion.scalar(select(Cliente).where(Cliente.rut == "76123456-0")) is not None


def test_cliente_inexistente_es_404(cliente_api: TestClient, usuarios: dict[str, Any]) -> None:
    r = cliente_api.get(f"{PREFIJO}/clientes/78456123-2", headers=usuarios["auth_uno"])
    assert r.status_code == 404


def test_sin_token_no_se_listan_clientes(cliente_api: TestClient) -> None:
    assert cliente_api.get(f"{PREFIJO}/clientes").status_code == 401
