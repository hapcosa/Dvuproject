"""Dependencias compartidas de la API: sesión y usuario autenticado.

El rol viaja dentro del token, pero el usuario se relee de la base en cada request:
un vendedor desvinculado deja de operar de inmediato, sin esperar a que expire su
token. Es una consulta por request y vale la pena.
"""

from __future__ import annotations

import uuid as uuid_lib
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.db.models import Usuario
from dvu.db.session import get_session
from dvu.seguridad import TokenInvalido, leer_token

SessionDep = Annotated[Session, Depends(get_session)]

#: `auto_error=False` para poder responder siempre con el mismo 401 y el header
#: `WWW-Authenticate`, en vez de mezclar 401 y 403 según falte o sobre el header.
_bearer = HTTPBearer(auto_error=False)

NO_AUTORIZADO = HTTPException(
    status.HTTP_401_UNAUTHORIZED,
    detail="Credenciales inválidas o vencidas",
    headers={"WWW-Authenticate": "Bearer"},
)


def usuario_actual(
    session: SessionDep,
    credenciales: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Usuario:
    if credenciales is None:
        raise NO_AUTORIZADO
    try:
        payload = leer_token(credenciales.credentials)
        uuid = uuid_lib.UUID(payload["sub"])
    except (TokenInvalido, KeyError, ValueError) as exc:
        raise NO_AUTORIZADO from exc

    usuario = session.scalar(select(Usuario).where(Usuario.uuid == uuid))
    if usuario is None or not usuario.activo:
        raise NO_AUTORIZADO
    return usuario


UsuarioDep = Annotated[Usuario, Depends(usuario_actual)]


def usuario_descargando(
    session: SessionDep,
    credenciales: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    token: Annotated[str | None, Query(description="Token de descarga, para bajar por URL")] = None,
) -> Usuario:
    """Como `usuario_actual`, pero acepta además el token corto de `POST /auth/descarga`.

    Es para los archivos que el navegador tiene que bajar navegando a la URL —el catálogo
    en PDF pesa decenas de MB y por `fetch` habría que juntarlo entero en memoria—, donde
    no hay forma de mandar el header `Authorization`. El token de la query dura minutos y
    sólo vale acá: `usuario_actual` sigue exigiendo uno de tipo `access`.
    """
    if credenciales is not None:
        return usuario_actual(session, credenciales)
    if token is None:
        raise NO_AUTORIZADO
    try:
        payload = leer_token(token, tipo="descarga")
        uuid = uuid_lib.UUID(payload["sub"])
    except (TokenInvalido, KeyError, ValueError) as exc:
        raise NO_AUTORIZADO from exc

    usuario = session.scalar(select(Usuario).where(Usuario.uuid == uuid))
    if usuario is None or not usuario.activo:
        raise NO_AUTORIZADO
    return usuario


DescargaDep = Annotated[Usuario, Depends(usuario_descargando)]


def exige_rol(*roles: str) -> Callable[[Usuario], Usuario]:
    """Dependencia que restringe un endpoint a ciertos roles.

    `admin` pasa siempre: es quien tiene que poder destrabar cualquier cosa.
    """

    def verificar(usuario: UsuarioDep) -> Usuario:
        if usuario.rol != "admin" and usuario.rol not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=f"Se requiere rol {' o '.join(roles)}",
            )
        return usuario

    return verificar
