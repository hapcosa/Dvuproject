"""Hash de contraseñas y tokens JWT.

Argon2id es el recomendado por OWASP y trae la sal incorporada en el propio hash,
así que no hay nada que guardar aparte. `necesita_rehash` permite re-hashear en el
siguiente login sin pedirle nada al usuario.

Los tokens llevan el `uuid` del usuario en `sub` —nunca el id secuencial, que filtra
cuántos usuarios hay— y el rol en `rol`, para no consultar la base en cada request.
El refresh se distingue por `tipo`: un access token no sirve para renovar.
"""

from __future__ import annotations

import uuid as uuid_lib
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from dvu.config import get_settings

TipoToken = Literal["access", "refresh"]

_hasher = PasswordHasher()


class TokenInvalido(Exception):
    """El token está vencido, adulterado o no es del tipo esperado."""


def hashear(password: str) -> str:
    return _hasher.hash(password)


def verificar(password: str, hash_guardado: str) -> bool:
    try:
        return _hasher.verify(hash_guardado, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def necesita_rehash(hash_guardado: str) -> bool:
    """True si el hash se generó con parámetros anteriores a los actuales."""
    try:
        return _hasher.check_needs_rehash(hash_guardado)
    except InvalidHashError:
        return True


def emitir_token(
    usuario_uuid: uuid_lib.UUID,
    rol: str,
    *,
    tipo: TipoToken = "access",
    ahora: datetime | None = None,
) -> str:
    cfg = get_settings()
    emision = ahora or datetime.now(UTC)
    duracion = (
        timedelta(minutes=cfg.access_token_minutes)
        if tipo == "access"
        else timedelta(days=cfg.refresh_token_days)
    )
    payload = {
        "sub": str(usuario_uuid),
        "rol": rol,
        "tipo": tipo,
        "iat": emision,
        "exp": emision + duracion,
    }
    return jwt.encode(payload, cfg.secret_key, algorithm=cfg.jwt_algorithm)


def leer_token(token: str, *, tipo: TipoToken = "access") -> dict[str, Any]:
    """Devuelve el payload verificado. Lanza `TokenInvalido` ante cualquier problema."""
    cfg = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token, cfg.secret_key, algorithms=[cfg.jwt_algorithm], options={"require": ["exp"]}
        )
    except jwt.PyJWTError as exc:
        raise TokenInvalido(str(exc)) from exc

    if payload.get("tipo") != tipo:
        raise TokenInvalido(f"se esperaba un token {tipo}")
    return payload
