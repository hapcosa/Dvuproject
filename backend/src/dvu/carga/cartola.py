"""Cartola de prueba para ensayar la conciliación sin agregador.

Genera un JSONL a partir de los pagos ya declarados en la base, imitando cómo llega un
abono real: la glosa del banco trae el RUT del que transfirió y el nº de operación va en
un campo aparte.

A propósito **no** es un espejo perfecto de los pagos: uno queda desfasado un día (el
banco acredita al día siguiente) y otro sin referencia, para que la bandeja de
excepciones se ejerza en desarrollo y no aparezca recién en producción.
"""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.db.models import Cliente, Pago


def cartola_de_prueba(session: Session, limite: int = 50) -> list[str]:
    filas = session.execute(
        select(Pago, Cliente)
        .join(Cliente, Pago.cliente_id == Cliente.id)
        .where(Pago.estado.in_(("declarado", "pendiente_revision")))
        .order_by(Pago.id)
        .limit(limite)
    ).all()

    lineas: list[str] = []
    for indice, (pago, cliente) in enumerate(filas):
        desfase = 1 if indice % 3 == 1 else 0
        sin_referencia = indice % 4 == 3
        lineas.append(
            json.dumps(
                {
                    "id": f"demo-{pago.id}",
                    "fecha": (pago.fecha_pago + timedelta(days=desfase)).isoformat(),
                    "monto_clp": int(pago.monto_clp),
                    "descripcion": f"TEF DE {cliente.rut} {cliente.razon_social[:40]}",
                    "referencia": None if sin_referencia else pago.referencia,
                    "rut_contraparte": cliente.rut,
                },
                ensure_ascii=False,
            )
        )

    # Un abono que no es de nadie: una devolución del banco. Tiene que quedar en la
    # bandeja sin inventarle un pago.
    if lineas:
        lineas.append(
            json.dumps(
                {
                    "id": "demo-ruido",
                    "fecha": filas[0][0].fecha_pago.isoformat(),
                    "monto_clp": 12345,
                    "descripcion": "ABONO REVERSA COMISION",
                    "referencia": None,
                    "rut_contraparte": None,
                },
                ensure_ascii=False,
            )
        )
    return lineas
