"""Datos de ejemplo."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dvu.carga.seed import PASSWORD_DEV, USUARIOS, SeedEnProduccion, sembrar
from dvu.config import Settings
from dvu.db.models import Cliente, Usuario
from dvu.domain.rut import es_valido
from dvu.seguridad import verificar

pytestmark = pytest.mark.integration


def test_sembrar_crea_usuarios_y_clientes(sesion: Session) -> None:
    resumen = sembrar(sesion)

    # Atado a la lista y no a un número: agregar un rol de ejemplo no debería romper
    # un test que no habla de roles.
    assert resumen.usuarios_creados == len(USUARIOS)
    assert resumen.clientes_creados == 3

    admin = sesion.scalar(select(Usuario).where(Usuario.email == "admin@dvu.cl"))
    assert admin is not None
    assert admin.rol == "admin"
    assert verificar(PASSWORD_DEV, admin.password_hash)
    # La contraseña nunca se guarda en claro.
    assert PASSWORD_DEV not in admin.password_hash


def test_los_rut_de_ejemplo_son_validos(sesion: Session) -> None:
    sembrar(sesion)

    ruts = list(sesion.scalars(select(Cliente.rut)))
    assert ruts
    assert all(es_valido(r) for r in ruts)


def test_los_clientes_quedan_asignados_al_vendedor(sesion: Session) -> None:
    sembrar(sesion)

    vendedor = sesion.scalar(select(Usuario).where(Usuario.rol == "vendedor"))
    assert vendedor is not None
    asignados = sesion.scalar(
        select(func.count()).select_from(Cliente).where(Cliente.vendedor_id == vendedor.id)
    )
    assert asignados == 3


def test_sembrar_dos_veces_no_duplica(sesion: Session) -> None:
    sembrar(sesion)
    sesion.flush()
    segunda = sembrar(sesion)

    assert segunda.usuarios_creados == 0
    assert segunda.clientes_creados == 0


def test_no_corre_en_produccion(sesion: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dvu.carga.seed.get_settings", lambda: Settings(env="production"))

    with pytest.raises(SeedEnProduccion):
        sembrar(sesion)
