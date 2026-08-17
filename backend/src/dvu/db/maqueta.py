"""El orden de las páginas de arte del catálogo, en un solo lugar.

Tres consumidores tienen que estar de acuerdo o el administrador ve una cosa y el PDF
sale con otra: la pantalla de administración —donde se arrastra—, el catálogo web y el
exportador (`carga.catalogo_impreso`). Por eso el criterio vive acá y no repetido en
cada uno.

El orden es en dos niveles:

1. **La sección**, que la fija el tipo y no se puede mover: la portada va al principio y
   la contraportada al final. Eso no es una preferencia, es lo que hace que un catálogo
   se lea como un catálogo, y dejarlo arrastrable sólo permitiría equivocarse.
2. **La posición dentro de la sección**, que sí es del administrador. Es la columna
   `orden`, y es la que cambia al arrastrar.

`(archivo, pagina)` queda de desempate y de procedencia —de qué PDF salió el recorte y
en qué página venía—, nunca de orden: dos portadas de dos PDF distintos son las dos la
página 1.
"""

from __future__ import annotations

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from dvu.db.models import CatalogoPagina

#: Las mismas tres que acepta la restricción de la tabla, en el orden en que se imprimen.
SECCIONES: tuple[str, ...] = ("portada", "promocion", "contraportada")

#: Para ordenar por sección en SQL. Un tipo desconocido —que la restricción no deja
#: entrar, pero por si acaso— cae al final en vez de mezclarse con la portada.
_RANGO = case(
    {tipo: posicion for posicion, tipo in enumerate(SECCIONES)},
    value=CatalogoPagina.tipo,
    else_=len(SECCIONES),
)


def ordenadas(consulta: Select[tuple[CatalogoPagina]]) -> Select[tuple[CatalogoPagina]]:
    """Aplica el orden de la maqueta a una consulta de páginas."""
    return consulta.order_by(
        _RANGO, CatalogoPagina.orden, CatalogoPagina.archivo, CatalogoPagina.pagina
    )


def paginas(session: Session, *, incluir_inactivas: bool = False) -> list[CatalogoPagina]:
    """Las páginas de arte en el orden en que van impresas."""
    consulta = select(CatalogoPagina)
    if not incluir_inactivas:
        consulta = consulta.where(CatalogoPagina.activa.is_(True))
    return list(session.scalars(ordenadas(consulta)))


def siguiente_orden(session: Session, tipo: str) -> int:
    """La posición que sigue dentro de la sección, para la página que se acaba de subir.

    Va al final de su sección: es lo que espera quien sube una oferta nueva, y si la
    quiere en otro lado la arrastra.
    """
    ultimo = session.scalar(
        select(func.max(CatalogoPagina.orden)).where(CatalogoPagina.tipo == tipo)
    )
    return (ultimo or 0) + 1


def numerar_faltantes(session: Session) -> int:
    """Le da posición a las páginas que entraron sin ella y devuelve cuántas fueron.

    Las crea `cargar-catalogo`, que sabe de qué PDF y de qué página salió cada recorte
    pero no dónde las quiere el administrador. Se van al final de su sección en el orden
    del PDF de origen, que es el que traían impresas.
    """
    session.flush()
    ultimos: dict[str, int] = {
        tipo: session.scalar(
            select(func.max(CatalogoPagina.orden)).where(CatalogoPagina.tipo == tipo)
        )
        or 0
        for tipo in SECCIONES
    }
    sin_orden = session.scalars(
        select(CatalogoPagina)
        .where(CatalogoPagina.orden == 0)
        .order_by(CatalogoPagina.archivo, CatalogoPagina.pagina)
    )
    numeradas = 0
    for pagina in sin_orden:
        ultimos[pagina.tipo] = ultimos.get(pagina.tipo, 0) + 1
        pagina.orden = ultimos[pagina.tipo]
        numeradas += 1
    return numeradas


def renumerar(session: Session, ids: list[int]) -> list[CatalogoPagina]:
    """Reasigna `orden` a las páginas de `ids`, en ese mismo orden.

    Numera de 1 en 1 y no deja huecos: el orden se reescribe entero en cada arrastre, así
    que no hace falta reservar espacio entre posiciones. Devuelve las páginas tocadas.

    No valida que sean todas de la misma sección — el arrastre puede mover una página de
    la portada a las ofertas, y en ese caso llegan dos llamadas, una por sección.
    """
    encontradas = {
        p.id: p for p in session.scalars(select(CatalogoPagina).where(CatalogoPagina.id.in_(ids)))
    }
    tocadas = []
    for posicion, pagina_id in enumerate(ids, start=1):
        pagina = encontradas.get(pagina_id)
        if pagina is None:
            continue
        pagina.orden = posicion
        tocadas.append(pagina)
    return tocadas


__all__ = [
    "SECCIONES",
    "numerar_faltantes",
    "ordenadas",
    "paginas",
    "renumerar",
    "siguiente_orden",
]
