"""Marcas del catálogo: nombrar lo que en el impreso es sólo un logo.

En el catálogo de papel la marca es el logo del proveedor, no su nombre escrito. El
extractor lo recorta bien —1275 productos tienen logo, en 220 imágenes distintas— pero
no puede leerlo: son imágenes anónimas. Ponerles nombre es trabajo humano, y por eso
vive en su propia tabla y no en `producto.marca`, que `make cargar-catalogo` reescribe
en cada recarga.

Acá va sólo lo que no toca la base ni el disco.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

#: Largo máximo del slug, igual que `categoria.slug`.
LARGO_SLUG: Final = 160

_NO_ALFANUMERICO = re.compile(r"[^a-z0-9]+")


def slug_de(nombre: str) -> str:
    """Convierte el nombre de una marca en su slug.

    Las marcas del rubro traen tildes, «ñ» y símbolos —`Sika®`, `Tigre Ñuble`,
    `3M`, `Cementos Bío-Bío`— y el slug es lo que va en la URL. Se quitan los
    diacríticos en vez de rechazarlos: `Bío-Bío` y `Bio-Bio` tienen que caer en el
    mismo slug, porque quien escribe la segunda forma está nombrando la misma marca
    y si no chocan quedan dos marcas para el mismo logo.

    Devuelve cadena vacía si no queda nada utilizable; quien llama decide qué hacer.
    """
    sin_tildes = unicodedata.normalize("NFKD", nombre)
    ascii_plano = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return _NO_ALFANUMERICO.sub("-", ascii_plano.lower()).strip("-")[:LARGO_SLUG].strip("-")
