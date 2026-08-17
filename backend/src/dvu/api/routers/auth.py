"""Autenticación.

El vendedor se autentica una vez y la app guarda el refresh token: en terreno no hay
señal para reintentar un login, así que el access token se renueva en cuanto vuelve
la conexión.
"""

from __future__ import annotations

import uuid as uuid_lib

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import select

from dvu.api.deps import NO_AUTORIZADO, SessionDep, UsuarioDep
from dvu.config import get_settings
from dvu.db.models import Usuario
from dvu.seguridad import (
    TokenInvalido,
    emitir_token,
    hashear,
    leer_token,
    necesita_rehash,
    verificar,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class Credenciales(BaseModel):
    email: EmailStr
    password: str


class Tokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 — nombre del esquema, no un secreto
    expires_in: int


class Refresh(BaseModel):
    refresh_token: str


class TokenDescarga(BaseModel):
    """Permiso de lectura de un rato para bajar un archivo por URL."""

    token: str
    expires_in: int


class UsuarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: uuid_lib.UUID
    email: str
    nombre: str
    rol: str


def _tokens(usuario: Usuario) -> Tokens:
    return Tokens(
        access_token=emitir_token(usuario.uuid, usuario.rol),
        refresh_token=emitir_token(usuario.uuid, usuario.rol, tipo="refresh"),
        expires_in=get_settings().access_token_minutes * 60,
    )


@router.post("/login", response_model=Tokens)
def login(credenciales: Credenciales, session: SessionDep) -> Tokens:
    usuario = session.scalar(select(Usuario).where(Usuario.email == credenciales.email))

    # Mismo mensaje para email inexistente y contraseña mala: no confirmamos qué
    # correos están registrados.
    if usuario is None or not usuario.activo:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")
    if not verificar(credenciales.password, usuario.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")

    if necesita_rehash(usuario.password_hash):
        usuario.password_hash = hashear(credenciales.password)

    return _tokens(usuario)


@router.post("/refresh", response_model=Tokens)
def refrescar(datos: Refresh, session: SessionDep) -> Tokens:
    try:
        payload = leer_token(datos.refresh_token, tipo="refresh")
        uuid = uuid_lib.UUID(payload["sub"])
    except (TokenInvalido, KeyError, ValueError) as exc:
        raise NO_AUTORIZADO from exc

    usuario = session.scalar(select(Usuario).where(Usuario.uuid == uuid))
    if usuario is None or not usuario.activo:
        raise NO_AUTORIZADO
    return _tokens(usuario)


@router.post("/descarga", response_model=TokenDescarga)
def token_de_descarga(usuario: UsuarioDep) -> TokenDescarga:
    """Un token corto para poner en la URL de una descarga grande.

    El `Authorization` no se puede mandar al navegar a una URL, y bajar el catálogo por
    `fetch` obliga a juntar decenas de MB en memoria antes de escribirlos: en un celular
    eso se cuelga o se cae. Con esto la web abre la URL y el navegador la descarga
    derecho a disco.
    """
    return TokenDescarga(
        token=emitir_token(usuario.uuid, usuario.rol, tipo="descarga"),
        expires_in=get_settings().descarga_token_minutes * 60,
    )


@router.get("/yo", response_model=UsuarioOut)
def yo(usuario: UsuarioDep) -> Usuario:
    return usuario
