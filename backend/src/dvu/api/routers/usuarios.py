"""Usuarios: quién entra al sistema y con qué rol.

Hasta ahora los usuarios salían del `make seed` o de la consola de la base, así que dar
de alta a un vendedor nuevo era una tarea de quien opera el repo, no del dueño. Acá está
para que la administración lo haga sola.

**No hay borrar**: un usuario se desactiva. Sus pedidos y sus comprobantes lo apuntan, y
borrar la fila dejaría documentos sin autor —el mismo criterio con el que un pedido
anulado se queda en la tabla—.
"""

from __future__ import annotations

import uuid as uuid_lib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import select

from dvu.api.deps import SessionDep, exige_rol
from dvu.db.models import Usuario
from dvu.domain.roles import ADMIN, ASIGNABLES, DESCRIPCION
from dvu.seguridad import hashear

router = APIRouter(prefix="/usuarios", tags=["usuarios"])

#: Sólo la administración. Un editor mantiene el catálogo; repartir accesos es otra cosa.
AdminDep = Annotated[Usuario, Depends(exige_rol(ADMIN))]

#: Piso de la contraseña. Corta no es una preferencia de estilo: estas cuentas entran a
#: los precios y a la cartera de clientes desde cualquier navegador de la calle.
MINIMO_PASSWORD = 10


def _validar_rol(rol: str) -> str:
    if rol not in ASIGNABLES:
        raise ValueError(f"Rol no asignable; se aceptan: {', '.join(ASIGNABLES)}")
    return rol


class UsuarioNuevo(BaseModel):
    email: EmailStr
    nombre: str = Field(min_length=2, max_length=160)
    rol: str
    password: str = Field(min_length=MINIMO_PASSWORD, max_length=128)

    _rol_valido = field_validator("rol")(_validar_rol)


class UsuarioCambio(BaseModel):
    """Todo opcional: se manda sólo lo que cambia."""

    nombre: str | None = Field(default=None, min_length=2, max_length=160)
    rol: str | None = None
    activo: bool | None = None
    password: str | None = Field(default=None, min_length=MINIMO_PASSWORD, max_length=128)

    @field_validator("rol")
    @classmethod
    def _rol_valido(cls, v: str | None) -> str | None:
        return None if v is None else _validar_rol(v)


class UsuarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: uuid_lib.UUID
    email: str
    nombre: str
    rol: str
    activo: bool


class RolOut(BaseModel):
    codigo: str
    descripcion: str


@router.get("/roles", response_model=list[RolOut])
def roles_asignables(usuario: AdminDep) -> list[RolOut]:
    """Los roles que se pueden dar, con qué hace cada uno.

    La descripción viaja desde el dominio para que la pantalla no la reescriba: quien
    reparte accesos elige por lo que la persona va a hacer, no por el código del rol.
    """
    return [RolOut(codigo=r, descripcion=DESCRIPCION[r]) for r in ASIGNABLES]


@router.get("", response_model=list[UsuarioOut])
def listar(
    session: SessionDep,
    usuario: AdminDep,
    incluir_inactivos: Annotated[bool, Query(description="También los desactivados")] = False,
) -> list[Usuario]:
    consulta = select(Usuario).order_by(Usuario.nombre)
    if not incluir_inactivos:
        consulta = consulta.where(Usuario.activo.is_(True))
    return list(session.scalars(consulta))


@router.post("", response_model=UsuarioOut, status_code=status.HTTP_201_CREATED)
def crear(datos: UsuarioNuevo, session: SessionDep, usuario: AdminDep) -> Usuario:
    email = datos.email.strip().lower()
    if session.scalar(select(Usuario).where(Usuario.email == email)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Ya hay un usuario con el correo {email}",
        )

    nuevo = Usuario(
        email=email,
        nombre=datos.nombre.strip(),
        rol=datos.rol,
        password_hash=hashear(datos.password),
        activo=True,
    )
    session.add(nuevo)
    session.flush()
    return nuevo


@router.patch("/{uuid}", response_model=UsuarioOut)
def cambiar(
    uuid: uuid_lib.UUID, datos: UsuarioCambio, session: SessionDep, usuario: AdminDep
) -> Usuario:
    objetivo = session.scalar(select(Usuario).where(Usuario.uuid == uuid))
    if objetivo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No existe ese usuario")

    # Quedarse fuera del sistema por un clic no debería ser posible, y con un solo
    # administrador no habría quién lo deshaga desde la web.
    if objetivo.id == usuario.id and datos.activo is False:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="No puedes desactivar tu propia cuenta",
        )
    # Los roles de `ASIGNABLES` no incluyen `admin`, así que cambiarle el rol a un
    # administrador lo degradaría sin vuelta desde la web.
    if objetivo.rol == ADMIN and datos.rol is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="El rol de un administrador no se cambia desde acá",
        )

    if datos.nombre is not None:
        objetivo.nombre = datos.nombre.strip()
    if datos.rol is not None:
        objetivo.rol = datos.rol
    if datos.activo is not None:
        objetivo.activo = datos.activo
    if datos.password is not None:
        objetivo.password_hash = hashear(datos.password)

    session.flush()
    return objetivo
