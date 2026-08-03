"""Pagos: declaración, comprobante y bandeja de excepciones."""

from __future__ import annotations

import io
import uuid as uuid_lib
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.almacenamiento import AlmacenLocal, get_almacen
from dvu.api.main import create_app
from dvu.db.models import Cliente, Pago, Pedido, PedidoLinea, Producto, Usuario
from dvu.db.session import get_session
from dvu.seguridad import emitir_token, hashear

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"
RUT = "76123456-0"


@pytest.fixture
def datos(sesion: Session) -> dict[str, Any]:
    vendedor = Usuario(email="v@test.cl", nombre="V", rol="vendedor", password_hash=hashear("x"))
    admin = Usuario(email="a@test.cl", nombre="A", rol="admin", password_hash=hashear("x"))
    sesion.add_all([vendedor, admin])
    sesion.flush()

    cliente = Cliente(rut=RUT, razon_social="FERRETERIA TEST SPA", vendedor_id=vendedor.id)
    producto = Producto(
        sku="DVU-PR49573",
        descripcion="LIQUIDO DE FRENO FEDERAL",
        multiplo_venta=12,
        precio_lista_clp=Decimal("1790"),
    )
    sesion.add_all([cliente, producto])
    sesion.flush()

    pedido = Pedido(
        client_uuid=uuid_lib.uuid4(),
        numero="P-2026-000001",
        cliente_id=cliente.id,
        vendedor_id=vendedor.id,
        origen="app_vendedor",
        estado="confirmado",
        neto_clp=Decimal("42960"),
        iva_clp=Decimal("8162"),
        total_clp=Decimal("51122"),
        lineas=[
            PedidoLinea(
                producto_id=producto.id,
                sku=producto.sku,
                descripcion=producto.descripcion,
                multiplo_venta=12,
                cantidad=24,
                precio_unitario_clp=Decimal("1790"),
                total_linea_clp=Decimal("42960"),
            )
        ],
    )
    sesion.add(pedido)
    sesion.flush()

    return {
        "cliente": cliente,
        "pedido": pedido,
        "vendedor": vendedor,
        "admin": admin,
        "auth_vendedor": {"Authorization": f"Bearer {emitir_token(vendedor.uuid, 'vendedor')}"},
        "auth_admin": {"Authorization": f"Bearer {emitir_token(admin.uuid, 'admin')}"},
    }


@pytest.fixture
def cliente_api_local(sesion: Session, tmp_path: Any) -> Any:
    """Cliente HTTP con el almacén en disco, para no necesitar MinIO en los tests."""
    app = create_app()
    app.dependency_overrides[get_session] = lambda: sesion
    app.dependency_overrides[get_almacen] = lambda: AlmacenLocal(tmp_path)
    with TestClient(app) as cliente:
        yield cliente
    app.dependency_overrides.clear()


def _payload(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "cliente_rut": RUT,
        "monto_clp": 51122,
        "fecha_pago": str(date(2026, 8, 1)),
        "metodo": "transferencia",
        "referencia": "OP-99887766",
    }
    base.update(extra)
    return base


def test_declarar_pago_queda_pendiente_de_verificacion(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    r = cliente_api.post(f"{PREFIJO}/pagos", json=_payload(), headers=datos["auth_vendedor"])

    assert r.status_code == 201
    cuerpo = r.json()
    # Declarado, no verificado: nadie miró todavía la cartola.
    assert cuerpo["estado"] == "declarado"
    assert cuerpo["monto_clp"] == 51122
    assert cuerpo["referencia"] == "OP-99887766"


def test_aplicar_a_pedidos(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/pagos",
        json=_payload(aplicaciones=[{"numero_pedido": "P-2026-000001", "monto_clp": 51122}]),
        headers=datos["auth_vendedor"],
    )

    assert r.status_code == 201
    assert len(r.json()["aplicaciones"]) == 1
    assert r.json()["aplicaciones"][0]["monto_clp"] == 51122


def test_no_se_puede_aplicar_mas_de_lo_pagado(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/pagos",
        json=_payload(
            monto_clp=10_000,
            aplicaciones=[{"numero_pedido": "P-2026-000001", "monto_clp": 51122}],
        ),
        headers=datos["auth_vendedor"],
    )
    assert r.status_code == 422


def test_no_se_puede_aplicar_a_un_pedido_de_otro_cliente(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    otro = Cliente(rut="77987654-3", razon_social="OTRA FERRETERIA LTDA")
    sesion.add(otro)
    sesion.flush()

    r = cliente_api.post(
        f"{PREFIJO}/pagos",
        json=_payload(
            cliente_rut="77987654-3",
            aplicaciones=[{"numero_pedido": "P-2026-000001", "monto_clp": 1000}],
        ),
        headers=datos["auth_vendedor"],
    )
    assert r.status_code == 404


def test_subir_comprobante(cliente_api_local: TestClient, datos: dict[str, Any]) -> None:
    pago_uuid = cliente_api_local.post(
        f"{PREFIJO}/pagos", json=_payload(), headers=datos["auth_vendedor"]
    ).json()["uuid"]

    r = cliente_api_local.post(
        f"{PREFIJO}/pagos/{pago_uuid}/comprobante",
        files={
            "archivo": ("transferencia.jpg", io.BytesIO(b"\xff\xd8\xff" + b"0" * 500), "image/jpeg")
        },
        headers=datos["auth_vendedor"],
    )

    assert r.status_code == 200
    assert r.json()["comprobante_key"] == f"comprobantes/{pago_uuid}.jpg"


def test_comprobante_rechaza_tipos_que_no_son_foto_ni_pdf(
    cliente_api_local: TestClient, datos: dict[str, Any]
) -> None:
    pago_uuid = cliente_api_local.post(
        f"{PREFIJO}/pagos", json=_payload(), headers=datos["auth_vendedor"]
    ).json()["uuid"]

    r = cliente_api_local.post(
        f"{PREFIJO}/pagos/{pago_uuid}/comprobante",
        files={"archivo": ("virus.exe", io.BytesIO(b"MZ"), "application/x-msdownload")},
        headers=datos["auth_vendedor"],
    )
    assert r.status_code == 415


def test_solo_admin_verifica_pagos(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    pago_uuid = cliente_api.post(
        f"{PREFIJO}/pagos", json=_payload(), headers=datos["auth_vendedor"]
    ).json()["uuid"]

    vendedor = cliente_api.post(
        f"{PREFIJO}/pagos/{pago_uuid}/estado",
        json={"estado": "verificado"},
        headers=datos["auth_vendedor"],
    )
    assert vendedor.status_code == 403

    admin = cliente_api.post(
        f"{PREFIJO}/pagos/{pago_uuid}/estado",
        json={"estado": "verificado"},
        headers=datos["auth_admin"],
    )
    assert admin.status_code == 200
    assert admin.json()["estado"] == "verificado"


def test_un_pago_que_no_cuadra_va_a_revision_y_no_se_borra(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    """Regla de dominio: la conciliación nunca es 100% automática."""
    pago_uuid = cliente_api.post(
        f"{PREFIJO}/pagos", json=_payload(), headers=datos["auth_vendedor"]
    ).json()["uuid"]

    r = cliente_api.post(
        f"{PREFIJO}/pagos/{pago_uuid}/estado",
        json={"estado": "pendiente_revision", "motivo": "No aparece en la cartola"},
        headers=datos["auth_admin"],
    )

    assert r.status_code == 200
    guardado = sesion.scalar(select(Pago).where(Pago.uuid == uuid_lib.UUID(pago_uuid)))
    assert guardado is not None
    assert guardado.estado == "pendiente_revision"


def test_estado_de_pago_inexistente_se_rechaza(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    pago_uuid = cliente_api.post(
        f"{PREFIJO}/pagos", json=_payload(), headers=datos["auth_vendedor"]
    ).json()["uuid"]

    r = cliente_api.post(
        f"{PREFIJO}/pagos/{pago_uuid}/estado",
        json={"estado": "inventado"},
        headers=datos["auth_admin"],
    )
    assert r.status_code == 422
