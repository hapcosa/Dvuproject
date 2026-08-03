# ADR-0001 — Stack: Python + FastAPI + PostgreSQL

- **Estado:** aceptado
- **Fecha:** 2026-08-03

## Contexto

Proyecto nuevo, sin código heredado. La primera entrega (Fase 0) es extracción de datos
desde PDF de 150 páginas; la segunda (Fase 1) es una API transaccional con app móvil
offline. Equipo pequeño.

## Decisión

**Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL 16.**

## Razones

- La Fase 0 es el camino crítico y el ecosistema de extracción de PDF (pdfplumber,
  PyMuPDF, camelot) es abrumadoramente Python. Elegir otro lenguaje obligaría a mantener
  dos runtimes desde el día 1.
- FastAPI da OpenAPI automático, lo que acelera el cliente Flutter (generación de SDK).
- PostgreSQL cubre lo transaccional, `pg_trgm` para la búsqueda difusa de productos (el
  vendedor escribe "codo media", no el SKU) y `jsonb` para las filas crudas del extractor.
- Pydantic v2 permite validar la misma forma de datos en la frontera HTTP y en el
  extractor.

## Alternativas descartadas

- **Node/TypeScript:** obligaría a un segundo runtime para el extractor.
- **Django:** el admin gratis es tentador, pero el ORM y el ciclo de request no encajan
  bien con el trabajo asíncrono de integraciones bancarias y DTE. Se prefiere un panel
  admin propio en Fase 1.
- **MySQL:** sin `jsonb` ni `pg_trgm` equivalentes de la misma calidad.

## Consecuencias

- `mypy` es obligatorio para compensar el tipado dinámico, en modo `strict` dentro de
  `dvu.domain`.
- La app móvil (Flutter/Dart) es un runtime aparte inevitable; se mitiga generando el
  cliente HTTP desde el OpenAPI de FastAPI.
