"""Pedidos: idempotencia offline, venta por múltiplos y máquina de estados."""

from __future__ import annotations

import uuid as uuid_lib
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dvu.db.models import Cliente, Pedido, Producto, Usuario
from dvu.seguridad import emitir_token, hashear

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"
RUT = "76123456-0"


@pytest.fixture
def datos(sesion: Session) -> dict[str, Any]:
    vendedor = Usuario(email="v@test.cl", nombre="V", rol="vendedor", password_hash=hashear("x"))
    bodega = Usuario(email="b@test.cl", nombre="B", rol="bodega", password_hash=hashear("x"))
    sesion.add_all([vendedor, bodega])
    sesion.flush()

    cliente = Cliente(rut=RUT, razon_social="FERRETERIA TEST SPA", vendedor_id=vendedor.id)
    # Caso real del catálogo: el líquido de freno se vende de a 12.
    producto = Producto(
        sku="DVU-PR49573",
        descripcion="LIQUIDO DE FRENO FEDERAL",
        unidad_venta="UNID",
        multiplo_venta=12,
        precio_lista_clp=Decimal("1790"),
    )
    sesion.add_all([cliente, producto])
    sesion.flush()

    return {
        "vendedor": vendedor,
        "bodega": bodega,
        "cliente": cliente,
        "producto": producto,
        "auth_vendedor": {"Authorization": f"Bearer {emitir_token(vendedor.uuid, 'vendedor')}"},
        "auth_bodega": {"Authorization": f"Bearer {emitir_token(bodega.uuid, 'bodega')}"},
    }


def _payload(cantidad: int = 24, client_uuid: uuid_lib.UUID | None = None) -> dict[str, Any]:
    return {
        "client_uuid": str(client_uuid or uuid_lib.uuid4()),
        "cliente_rut": RUT,
        "lineas": [{"sku": "DVU-PR49573", "cantidad": cantidad}],
    }


def test_crear_pedido_congela_precio_y_calcula_iva(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    r = cliente_api.post(f"{PREFIJO}/pedidos", json=_payload(), headers=datos["auth_vendedor"])

    assert r.status_code == 201
    cuerpo = r.json()
    assert cuerpo["estado"] == "enviado"
    assert cuerpo["origen"] == "app_vendedor"
    assert cuerpo["numero"].startswith("P-")
    assert cuerpo["neto_clp"] == 24 * 1790
    assert cuerpo["iva_clp"] == round(24 * 1790 * 0.19)
    assert cuerpo["total_clp"] == cuerpo["neto_clp"] + cuerpo["iva_clp"]
    # El precio queda escrito en la línea, no referenciado al catálogo.
    assert cuerpo["lineas"][0]["precio_unitario_clp"] == 1790


def test_reenvio_con_el_mismo_client_uuid_no_duplica(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    """El caso central de la app offline: se reenvía hasta recibir confirmación."""
    cuerpo = _payload()

    primera = cliente_api.post(f"{PREFIJO}/pedidos", json=cuerpo, headers=datos["auth_vendedor"])
    segunda = cliente_api.post(f"{PREFIJO}/pedidos", json=cuerpo, headers=datos["auth_vendedor"])

    assert primera.status_code == 201
    assert segunda.status_code == 200
    assert primera.json()["numero"] == segunda.json()["numero"]
    assert sesion.scalar(select(func.count()).select_from(Pedido)) == 1


def test_cantidad_que_no_es_multiplo_se_rechaza_con_sugerencia(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/pedidos", json=_payload(cantidad=7), headers=datos["auth_vendedor"]
    )

    assert r.status_code == 422
    detalle = r.json()["detail"][0]
    assert detalle["sku"] == "DVU-PR49573"
    assert detalle["multiplo_venta"] == 12
    # Sugerencia, no corrección: el vendedor decide.
    assert detalle["cantidad_sugerida"] == 12


def test_se_reportan_todas_las_lineas_malas_de_una_vez(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """Reenviar cuesta señal: el vendedor debe ver todos los errores en un viaje."""
    cuerpo = _payload()
    cuerpo["lineas"] = [
        {"sku": "DVU-PR49573", "cantidad": 7},
        {"sku": "DVU-NO-EXISTE", "cantidad": 12},
    ]

    r = cliente_api.post(f"{PREFIJO}/pedidos", json=cuerpo, headers=datos["auth_vendedor"])

    assert r.status_code == 422
    assert len(r.json()["detail"]) == 2


def test_cliente_inexistente_da_404(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    cuerpo = _payload()
    cuerpo["cliente_rut"] = "99999999-9"

    r = cliente_api.post(f"{PREFIJO}/pedidos", json=cuerpo, headers=datos["auth_vendedor"])
    assert r.status_code == 404


def test_crear_pedido_exige_autenticacion(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    assert cliente_api.post(f"{PREFIJO}/pedidos", json=_payload()).status_code == 401


def test_bodega_no_crea_pedidos(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    r = cliente_api.post(f"{PREFIJO}/pedidos", json=_payload(), headers=datos["auth_bodega"])
    assert r.status_code == 403


def test_el_vendedor_solo_ve_sus_pedidos(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    cliente_api.post(f"{PREFIJO}/pedidos", json=_payload(), headers=datos["auth_vendedor"])

    otro = Usuario(email="o@test.cl", nombre="O", rol="vendedor", password_hash=hashear("x"))
    sesion.add(otro)
    sesion.flush()
    auth_otro = {"Authorization": f"Bearer {emitir_token(otro.uuid, 'vendedor')}"}

    assert (
        cliente_api.get(f"{PREFIJO}/pedidos", headers=datos["auth_vendedor"]).json()["total"] == 1
    )
    assert cliente_api.get(f"{PREFIJO}/pedidos", headers=auth_otro).json()["total"] == 0
    # Bodega ve todo.
    assert cliente_api.get(f"{PREFIJO}/pedidos", headers=datos["auth_bodega"]).json()["total"] == 1


def test_avanzar_estado_deja_bitacora(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    numero = cliente_api.post(
        f"{PREFIJO}/pedidos", json=_payload(), headers=datos["auth_vendedor"]
    ).json()["numero"]

    r = cliente_api.post(
        f"{PREFIJO}/pedidos/{numero}/estado",
        json={"estado": "confirmado"},
        headers=datos["auth_bodega"],
    )

    assert r.status_code == 200
    assert r.json()["estado"] == "confirmado"
    eventos = r.json()["eventos"]
    assert [e["estado_nuevo"] for e in eventos] == ["enviado", "confirmado"]


def test_transicion_invalida_da_409(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    numero = cliente_api.post(
        f"{PREFIJO}/pedidos", json=_payload(), headers=datos["auth_vendedor"]
    ).json()["numero"]

    # No se puede saltar de 'enviado' a 'despachado'.
    r = cliente_api.post(
        f"{PREFIJO}/pedidos/{numero}/estado",
        json={"estado": "despachado"},
        headers=datos["auth_bodega"],
    )
    assert r.status_code == 409


def test_anular_exige_motivo(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    numero = cliente_api.post(
        f"{PREFIJO}/pedidos", json=_payload(), headers=datos["auth_vendedor"]
    ).json()["numero"]

    sin_motivo = cliente_api.post(
        f"{PREFIJO}/pedidos/{numero}/estado",
        json={"estado": "anulado"},
        headers=datos["auth_bodega"],
    )
    assert sin_motivo.status_code == 422

    con_motivo = cliente_api.post(
        f"{PREFIJO}/pedidos/{numero}/estado",
        json={"estado": "anulado", "motivo": "El cliente se arrepintió"},
        headers=datos["auth_bodega"],
    )
    assert con_motivo.status_code == 200
    assert con_motivo.json()["estado"] == "anulado"


def test_un_vendedor_no_ve_el_detalle_de_otro(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    numero = cliente_api.post(
        f"{PREFIJO}/pedidos", json=_payload(), headers=datos["auth_vendedor"]
    ).json()["numero"]

    otro = Usuario(email="o2@test.cl", nombre="O", rol="vendedor", password_hash=hashear("x"))
    sesion.add(otro)
    sesion.flush()

    r = cliente_api.get(
        f"{PREFIJO}/pedidos/{numero}",
        headers={"Authorization": f"Bearer {emitir_token(otro.uuid, 'vendedor')}"},
    )
    assert r.status_code == 403
