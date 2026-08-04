"""Siembra el árbol de categorías y clasifica el catálogo.

Separado de `dvu.domain.categorias` a propósito: allá están las reglas, que son datos
puros y se prueban con una lista de strings; acá está lo que toca la base.

La regla que gobierna todo este módulo: **la clasificación automática no pisa lo que
asignó una persona.** Por defecto sólo toca los productos sin categoría. `--reclasificar`
existe para cuando se cambian las reglas, y es explícito porque destruye correcciones.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.db.models import Categoria, Producto
from dvu.domain.categorias import CATEGORIAS, clasificar


@dataclass
class ResumenClasificacion:
    categorias_creadas: int = 0
    clasificados: int = 0
    #: Ya tenían categoría y no se tocaron (salvo `reclasificar=True`).
    respetados: int = 0
    #: Ninguna regla coincidió. Siguen siendo buscables por texto.
    sin_categoria: int = 0
    #: Muestra de descripciones sin clasificar, para decidir qué regla falta.
    ejemplos_sin_categoria: list[str] = field(default_factory=list)

    @property
    def cobertura(self) -> float:
        total = self.clasificados + self.respetados + self.sin_categoria
        return 100.0 * (total - self.sin_categoria) / total if total else 0.0

    def resumen(self) -> str:
        return (
            f"Categorías creadas: {self.categorias_creadas}\n"
            f"Productos clasificados: {self.clasificados}\n"
            f"Ya tenían categoría (respetados): {self.respetados}\n"
            f"Sin categoría: {self.sin_categoria}\n"
            f"Cobertura: {self.cobertura:.1f} %"
        )


MAX_EJEMPLOS = 20


def sembrar_categorias(session: Session) -> int:
    """Crea las categorías que falten. Idempotente: repetirlo no duplica ni renombra.

    No renombra a propósito: si el administrador le cambió el nombre a una categoría
    desde `/admin`, ese nombre es el que usa la fuerza de venta.
    """
    existentes = {c.slug for c in session.scalars(select(Categoria))}
    creadas = 0

    for orden, definicion in enumerate(CATEGORIAS):
        if definicion.slug in existentes:
            continue
        session.add(Categoria(slug=definicion.slug, nombre=definicion.nombre, orden=orden))
        creadas += 1

    session.flush()
    return creadas


def clasificar_catalogo(session: Session, *, reclasificar: bool = False) -> ResumenClasificacion:
    resumen = ResumenClasificacion(categorias_creadas=sembrar_categorias(session))
    por_slug = {c.slug: c for c in session.scalars(select(Categoria))}

    for producto in session.scalars(select(Producto)):
        if producto.categoria_id is not None and not reclasificar:
            resumen.respetados += 1
            continue

        slug = clasificar(producto.descripcion)
        if slug is None:
            # Reclasificar tampoco deja huérfano lo que ya estaba clasificado: si la
            # regla nueva no coincide, se conserva la categoría que tenía.
            if producto.categoria_id is not None:
                resumen.respetados += 1
                continue
            resumen.sin_categoria += 1
            if len(resumen.ejemplos_sin_categoria) < MAX_EJEMPLOS:
                resumen.ejemplos_sin_categoria.append(producto.descripcion)
            continue

        producto.categoria = por_slug[slug]
        resumen.clasificados += 1

    session.flush()
    return resumen
