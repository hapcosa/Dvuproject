"""Categorías del catálogo y clasificación por descripción.

**El PDF no trae categorías.** Sus páginas sólo tienen el folio y los títulos de columna
(`Código`, `Imagen`, `Descripción`, …); no hay un encabezado de sección que se pueda
extraer. Así que el árbol no se descubre: se define acá, a mano, con el vocabulario que
usa el catálogo real.

La clasificación es por **palabras clave explícitas sobre la descripción**. Tres cosas la
gobiernan:

1. **Lo que no coincide no se clasifica.** Queda sin categoría y se reporta. Una
   categoría inventada es peor que ninguna: el vendedor navega el árbol, no encuentra lo
   que sabe que existe, y deja de confiar en el árbol completo.
2. **La primera regla que coincide gana**, y el orden va de la familia más específica a
   la más genérica. «CAJA P/INTERRUPTOR» es eléctrica y «CAJA HERRAMIENTA» no, y las dos
   empiezan igual: lo que decide es qué otra palabra trae.
3. **La persona manda sobre la regla.** Esto propone; el administrador corrige desde
   `/admin` y la corrección no se vuelve a pisar (ver `dvu.carga.categorias`).

Módulo puro: sin I/O y sin sesión. Se puede probar con una lista de strings.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

__all__ = ["CATEGORIAS", "Categoria", "clasificar", "normalizar", "slugs"]


@dataclass(frozen=True, slots=True)
class Categoria:
    """Una rama del árbol. Plano por ahora: el catálogo no da para dos niveles."""

    slug: str
    nombre: str
    #: Palabras que la identifican en la descripción. Se comparan por palabra completa
    #: sobre el texto normalizado (mayúsculas, sin tildes).
    claves: tuple[str, ...]


# El orden es la regla de desempate y por eso es parte del dato, no un detalle.
# De lo más específico (un anzuelo sólo puede ser de pesca) a lo más genérico
# («herramientas» se lleva todo lo que quedó sin dueño y es evidentemente una).
CATEGORIAS: tuple[Categoria, ...] = (
    Categoria(
        "pesca",
        "Pesca y náutica",
        (
            "ANZUELO",
            "DESTORCEDOR",
            "SENUELO",
            "PLOMADA",
            "BOYA",
            "CARRETE",
            "NYLON",
            "REEL",
            "CANA DE PESCAR",
            "MARINO",
            "ROBALERO",
        ),
    ),
    Categoria(
        "automotriz",
        "Automotriz y lubricantes",
        (
            "ACEITE",
            "LUBRICANTE",
            "GRASA",
            "REFRIGERANTE",
            "ANTICONGELANTE",
            "LIQUIDO DE FRENO",
            "BUJIA",
            "LIMPIAPARABRISAS",
            "ANTICORROSIVO",
            "DESENGRASANTE",
            "CYCLE",
            "COOLANT",
        ),
    ),
    Categoria(
        "seguridad",
        "Seguridad y protección",
        (
            "GUANTE",
            "MASCARILLA",
            "RESPIRADOR",
            "ANTIPARRA",
            "ANTEPARRA",
            "CASCO",
            "ARNES",
            "PROTECTOR AUDITIVO",
            "TAPON AUDITIVO",
            "COLETO",
            "PECHERA",
            "BOTIQUIN",
        ),
    ),
    Categoria(
        "electricidad",
        "Electricidad e iluminación",
        (
            "INTERRUPTOR",
            "ENCHUFE",
            "ALARGADOR",
            "FOCO",
            "AMPOLLETA",
            "CABLE",
            "PORTALAMPARA",
            "SOQUETE",
            "LINTERNA",
            "AUTOMATICO",
            "ZAPATILLA",
            "TIMBRE",
            "CANALETA ELECTRICA",
            "HUINCHA AISLADORA",
            "CINTA AISLANTE",
        ),
    ),
    Categoria(
        "pinturas",
        "Pinturas y adhesivos",
        (
            "PINTURA",
            "ESMALTE",
            "LATEX",
            "BARNIZ",
            "DILUYENTE",
            "THINNER",
            "AGUARRAS",
            "BROCHA",
            "RODILLO",
            "SPRAY",
            "SILICONA",
            "ADHESIVO",
            "PEGAMENTO",
            "SELLANTE",
            "MASILLA",
            "ESPATULA",
            "MASKING",
            "IMPRIMANTE",
            "OLEO",
        ),
    ),
    Categoria(
        "abrasivos",
        "Discos, brocas y abrasivos",
        (
            "DISCO",
            "BROCA",
            "LIJA",
            "ESMERIL",
            "SIERRA COPA",
            "HOJA SIERRA",
            "HOJA DE SIERRA",
            "FRESA",
            "PULIDORA",
            "COPA DIAMANTADA",
        ),
    ),
    Categoria(
        "fijaciones",
        "Fijaciones y tornillería",
        (
            "CLAVO",
            "TORNILLO",
            "PERNO",
            "TUERCA",
            "GOLILLA",
            "REMACHE",
            "TARUGO",
            "ANCLAJE",
            "ESPARRAGO",
            "CORCHETE",
            "GRAPA",
            "AMARRA",
        ),
    ),
    Categoria(
        "cerrajeria",
        "Cerrajería y herrajes",
        (
            "CERRADURA",
            "CANDADO",
            "MANILLA",
            "BISAGRA",
            "POMEL",
            "PICAPORTE",
            "PORTACANDADO",
            "CERROJO",
            "MOSQUETON",
            "CANCAMO",
            "GANCHO",
            "ARGOLLA",
            "TIRADOR",
            "CORREDERA",
            "RIEL",
            "PERILLA",
            "CORTINA",
        ),
    ),
    Categoria(
        "gasfiteria",
        "Gasfitería",
        (
            "CODO",
            "TEE",
            "COPLA",
            "UNION",
            "REDUCCION",
            "NIPLE",
            "TUBO",
            "FLEXIBLE",
            "MONOMANDO",
            "VALVULA",
            "SIFON",
            "TEFLON",
            "CURVA",
            "MANGUERA",
            "ABRAZADERA",
            "TERMINAL",
            "TAPA GORRO",
            "DESAGUE",
            "LAVAPLATOS",
            "LAVAMANOS",
            "ESTANQUE",
            "DUCHA",
            "BUSHING",
            "LLAVE DE PASO",
            "LLAVE PASO",
            "LLAVE ANGULAR",
            "LLAVE JARDIN",
            "LLAVE CAMPANA",
            "LLAVE LAVAPLATOS",
            "LLAVE LAVAMANOS",
            "PPR",
            "ELECTROBOMBA",
            "BOMBA",
            "FILTRO AGUA",
            "WC",
            "FLOTADOR",
        ),
    ),
    Categoria(
        "herramientas",
        "Herramientas manuales",
        (
            "MARTILLO",
            "COMBO",
            "SERRUCHO",
            "ALICATE",
            "DESTORNILLADOR",
            "TIJERA",
            "LIMA",
            "CUCHILLO",
            "CUCHILLA",
            "HUINCHA",
            "ESCUADRA",
            "NIVEL",
            "PRENSA",
            "HACHA",
            "PALA",
            "CHUZO",
            "LLANA",
            "CUCHARA",
            "GUBIA",
            "FORMON",
            "CEPILLO CARPINTERO",
            "LLAVE REGULABLE",
            "LLAVE PUNTA",
            "LLAVE CORONA",
            "LLAVE FRANCESA",
            "LLAVE ALLEN",
            "LLAVE INGLESA",
            "LLAVE GASFITER",
            "LLAVE DE TUBO",
            "HERRAMIENTA",
            "CARRETILLA",
            "DADO",
            "CHAIRA",
            "MOTOSIERRA",
            "MACHETE",
            "NAVAJA",
        ),
    ),
)

#: Índice por slug, para no recorrer la tupla en cada consulta.
_POR_SLUG = {c.slug: c for c in CATEGORIAS}

#: `\b` no sirve: las claves de varias palabras («TAPA GORRO») tienen espacios y algunas
#: descripciones traen la palabra pegada a un signo («CODO 90°»). El límite se define
#: como inicio/fin o cualquier cosa que no sea letra o dígito.
#:
#: El `(?:ES|S)?` es indispensable, no una comodidad: el catálogo alterna singular y
#: plural en la misma familia («JGO DESTORNILLADOR 6 PZ» y «JGO DESTORNILLADORES DE
#: PRECISION»). Sin eso, la mitad de una familia queda sin categoría.
_PATRONES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        categoria.slug,
        re.compile(
            "(?:^|[^A-Z0-9])(?:"
            + "|".join(re.escape(k) for k in categoria.claves)
            + ")(?:ES|S)?(?![A-Z0-9])"
        ),
    )
    for categoria in CATEGORIAS
)


def slugs() -> tuple[str, ...]:
    return tuple(c.slug for c in CATEGORIAS)


def normalizar(texto: str) -> str:
    """Mayúsculas y sin tildes: el catálogo escribe «REDUCCIÓN» y «REDUCCION» indistinto."""
    descompuesto = unicodedata.normalize("NFD", texto.upper())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def clasificar(descripcion: str) -> str | None:
    """Devuelve el slug de la categoría, o `None` si ninguna regla coincide.

    `None` es un resultado legítimo y esperable, no un error: hay familias enteras del
    catálogo (menaje, jardín, camping) que todavía no tienen regla. Quedan sin categoría
    y siguen apareciendo en la búsqueda por texto, que es como se busca hoy.
    """
    texto = normalizar(descripcion)
    for slug, patron in _PATRONES:
        if patron.search(texto):
            return slug
    return None
