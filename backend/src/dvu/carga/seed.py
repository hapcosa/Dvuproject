"""Datos de ejemplo para desarrollo.

Sólo usuarios y clientes: el catálogo real se carga con `dvu cargar-catalogo`, que
es el camino que también se usa en producción. Nunca inventamos productos falsos —
confundirlos con los reales en una demo sería peor que no tener datos.

Se niega a correr en producción: las contraseñas son públicas.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.config import get_settings
from dvu.db.models import Cliente, Usuario
from dvu.domain.roles import ADMIN, BODEGA, EDITOR, VENDEDOR
from dvu.domain.rut import normalizar
from dvu.seguridad import hashear

#: Contraseña única de desarrollo. Es pública a propósito y por eso el seed aborta
#: si el entorno es producción.
PASSWORD_DEV = "dvu-dev-1234"  # noqa: S105 — pública a propósito, sólo para desarrollo

USUARIOS: list[tuple[str, str, str]] = [
    ("admin@dvu.cl", "Administración DVU", ADMIN),
    ("editor@dvu.cl", "Editor de Catálogo", EDITOR),
    ("vendedor@dvu.cl", "Vendedor de Ejemplo", VENDEDOR),
    ("bodega@dvu.cl", "Bodega DVU", BODEGA),
]

CLIENTES: list[tuple[str, str, str, str]] = [
    ("76123456-0", "FERRETERIA EL MARTILLO SPA", "Maipú", "contado"),
    ("77987654-3", "COMERCIAL CONSTRUYE LTDA", "San Bernardo", "credito_30"),
    ("78456123-2", "FERRETERIA LOS ANDES EIRL", "Puente Alto", "contado"),
]


@dataclass(slots=True)
class ResumenSeed:
    usuarios_creados: int = 0
    clientes_creados: int = 0

    def resumen(self) -> str:
        return (
            "\n═══ Datos de ejemplo ═══\n"
            f"  Usuarios creados : {self.usuarios_creados}\n"
            f"  Clientes creados : {self.clientes_creados}\n"
            f"  Contraseña       : {PASSWORD_DEV}\n"
        )


class SeedEnProduccion(RuntimeError):
    pass


def sembrar(session: Session) -> ResumenSeed:
    if get_settings().es_produccion:
        raise SeedEnProduccion("El seed usa contraseñas públicas: no corre en producción")

    resumen = ResumenSeed()
    hash_dev = hashear(PASSWORD_DEV)

    vendedor: Usuario | None = None
    for email, nombre, rol in USUARIOS:
        usuario = session.scalar(select(Usuario).where(Usuario.email == email))
        if usuario is None:
            usuario = Usuario(email=email, nombre=nombre, rol=rol, password_hash=hash_dev)
            session.add(usuario)
            resumen.usuarios_creados += 1
        if rol == "vendedor":
            vendedor = usuario

    session.flush()

    for rut, razon_social, comuna, condicion in CLIENTES:
        rut_norm = normalizar(rut)
        if session.scalar(select(Cliente).where(Cliente.rut == rut_norm)) is not None:
            continue
        session.add(
            Cliente(
                rut=rut_norm,
                razon_social=razon_social,
                comuna=comuna,
                ciudad="Santiago",
                condicion_pago=condicion,
                vendedor_id=vendedor.id if vendedor else None,
            )
        )
        resumen.clientes_creados += 1

    session.flush()
    return resumen
