"""Los roles del sistema y qué puede hacer cada uno.

**Todos son de gente que trabaja en DVU.** Este sistema es interno: no hay cuentas para
las ferreterías que compran. Existió un rol `cliente` pensando en que el ferretero armara
su propio pedido, pero eso es un ecommerce y sería otro servidor y otro stack —una idea
que todavía no se sabe si se hará—. Mientras tanto, un rol así en esta base es una cuenta
externa dentro del sistema de la casa.

Vive en `domain/` y no en la capa de API porque la respuesta a «¿quién puede editar el
catálogo?» es del negocio, no del framework. Y vive en **un solo archivo** porque la
lista estaba repartida en cuatro: el `CheckConstraint` de `usuario`, los `exige_rol` de
cada router, el `PAGINAS` del JavaScript y el seed. Agregar un rol obligaba a acordarse
de los cuatro, y el que se olvidaba no fallaba: dejaba a alguien sin poder entrar.
"""

from __future__ import annotations

from typing import Final

ADMIN: Final = "admin"
EDITOR: Final = "editor"
VENDEDOR: Final = "vendedor"
BODEGA: Final = "bodega"

#: Todos los roles válidos. El `CheckConstraint` de la tabla `usuario` los repite —una
#: base de datos no importa Python— pero la migración sale de acá.
ROLES: Final[tuple[str, ...]] = (ADMIN, EDITOR, VENDEDOR, BODEGA)

#: Qué hace cada uno, en las palabras del negocio. Es lo que se muestra al crear un
#: usuario: quien administra elige por lo que la persona va a hacer, no por el código.
DESCRIPCION: Final[dict[str, str]] = {
    ADMIN: "Todo: cobranza, facturación, usuarios y catálogo.",
    EDITOR: "Edita el catálogo y lo imprime. No ve cobranza ni facturación.",
    VENDEDOR: "Arma pedidos en terreno y registra los comprobantes de pago.",
    BODEGA: "Prepara y despacha los pedidos.",
}

#: Los que el administrador puede crear desde la web.
#:
#: `admin` queda fuera a propósito: darse compañía en el rol que puede todo no debería
#: ser un formulario más de la pantalla, y hoy no hace falta —el administrador es el
#: dueño—. Se crea por `dvu crear-usuario`, que exige estar en el servidor.
#: `bodega` tampoco está: no se pidió, y un rol que nadie usa en una lista desplegable
#: es una forma de equivocarse.
ASIGNABLES: Final[tuple[str, ...]] = (VENDEDOR, EDITOR)
