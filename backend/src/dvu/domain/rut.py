"""RUT chileno: normalización y validación por módulo 11.

Todo cliente de DVU es una empresa con RUT, y ese RUT va en la factura electrónica.
Un RUT mal guardado se transforma en un DTE rechazado por el SII, así que se valida
en el borde de entrada, no al facturar.
"""

from __future__ import annotations

import re

_RE_RUT = re.compile(r"^(\d{1,8})-?([\dkK])$")


class RutInvalido(ValueError):
    pass


def limpiar(rut: str) -> str:
    """Quita puntos, espacios y guiones. Deja el dígito verificador en mayúscula."""
    return re.sub(r"[.\s\-]", "", rut).upper()


def digito_verificador(numero: int) -> str:
    """Módulo 11.

    >>> digito_verificador(76123456)
    '0'
    """
    suma = 0
    multiplicador = 2
    for digito in reversed(str(numero)):
        suma += int(digito) * multiplicador
        multiplicador = 2 if multiplicador == 7 else multiplicador + 1

    resto = 11 - (suma % 11)
    if resto == 11:
        return "0"
    if resto == 10:
        return "K"
    return str(resto)


def es_valido(rut: str) -> bool:
    try:
        normalizar(rut)
    except RutInvalido:
        return False
    return True


def normalizar(rut: str) -> str:
    """Devuelve el RUT en la forma canónica `76123456-0`.

    >>> normalizar("76.123.456-0")
    '76123456-0'
    >>> normalizar("761234560")
    '76123456-0'
    >>> normalizar("76123456-2")
    Traceback (most recent call last):
    ...
    dvu.domain.rut.RutInvalido: Dígito verificador incorrecto para 76123456: es 0
    """
    limpio = limpiar(rut)
    m = _RE_RUT.match(limpio)
    if not m:
        raise RutInvalido(f"Formato de RUT no reconocido: {rut!r}")

    numero, dv = int(m.group(1)), m.group(2).upper()
    esperado = digito_verificador(numero)
    if dv != esperado:
        raise RutInvalido(f"Dígito verificador incorrecto para {numero}: es {esperado}")

    return f"{numero}-{dv}"


def formatear(rut: str) -> str:
    """Con puntos, para mostrar al usuario.

    >>> formatear("76123456-0")
    '76.123.456-0'
    """
    numero, dv = normalizar(rut).split("-")
    return f"{int(numero):,}".replace(",", ".") + f"-{dv}"
