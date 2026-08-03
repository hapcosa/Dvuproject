# Arquitectura

## Principios

1. **Monolito modular, no microservicios.** El volumen de DVU (decenas de vendedores,
   miles de pedidos/mes) no justifica la complejidad operacional de servicios
   distribuidos. Los módulos están separados por paquete y por frontera de dominio, de
   modo que extraer uno más adelante sea mecánico si algún día hace falta.
2. **Dominio sin I/O.** `dvu.domain` no importa SQLAlchemy, ni HTTP, ni el reloj del
   sistema directamente. Es lógica pura, testeable sin contenedores, con `mypy --strict`.
3. **Todo integrable es un adaptador.** Banco, SII y WhatsApp viven detrás de un
   `Protocol` con implementación fake para tests. Cambiar Fintoc por Floid no debe tocar
   el dominio.
4. **Idempotencia por defecto.** La app del vendedor es offline y reintenta. Toda
   escritura acepta una clave de idempotencia.
5. **Nada se borra.** Pedidos y pagos se anulan con estado, nunca con `DELETE`. Auditoría
   completa: quién, cuándo, qué cambió.

## Capas

```
 ┌──────────────────────────────────────────────────┐
 │  dvu.api        FastAPI: routers, schemas, deps  │  HTTP
 ├──────────────────────────────────────────────────┤
 │  dvu.domain     Reglas de negocio puras          │  ← sin I/O, mypy strict
 ├──────────────────────────────────────────────────┤
 │  dvu.db         SQLAlchemy: modelos, repos       │  Persistencia
 ├──────────────────────────────────────────────────┤
 │  dvu.extractor  PDF -> filas normalizadas        │  Fase 0, ejecutable aparte
 │  dvu.workers    Jobs arq (extracción, DTE, banco)│  Async
 └──────────────────────────────────────────────────┘
```

Regla de dependencia: **hacia adentro**. `api` → `domain` ← `db`. `domain` no importa a
nadie de las otras capas.

## Servicios (Docker Compose)

| Servicio | Imagen | Puerto | Rol |
|---|---|---|---|
| `api` | build local | 8000 | FastAPI |
| `worker` | build local (misma imagen) | — | Jobs arq |
| `db` | `postgres:16-alpine` | 5432 | BD principal |
| `redis` | `redis:7-alpine` | 6379 | Cola de jobs, cache |
| `minio` | `minio/minio` | 9000/9001 | Imágenes de producto, comprobantes |

**Sin Kubernetes** — decisión explícita, ver [ADR-0002](adr/0002-sin-kubernetes.md).
Producción: un VPS con Docker Compose, Caddy como reverse proxy con TLS automático,
backups de Postgres a S3. Si el sistema crece, el camino es escalar verticalmente y
después considerar orquestación, no antes.

## Cultura de desarrollo (DevOps)

| Práctica | Herramienta | Dónde |
|---|---|---|
| Formato + lint | `ruff` | pre-commit + CI |
| Tipos | `mypy` (strict en `domain/`) | pre-commit + CI |
| Tests | `pytest`, coverage mínimo 70% | CI |
| Migraciones | `alembic`, una por PR, reversible | CI valida `upgrade`+`downgrade` |
| Secretos | `.env` fuera de git, `.env.example` documentado | — |
| Build | Dockerfile multi-stage, imagen no-root | CI |
| CI | GitHub Actions en cada push/PR | `.github/workflows/ci.yml` |
| Salud | `/health` (liveness) y `/health/ready` (dependencias) | api |
| Logs | JSON estructurado a stdout, con `request_id` | `dvu.api.logging` |
| Errores | Sentry (opcional, por env var) | — |

**Definition of Done** de un cambio: lint + tipos + tests verdes, migración reversible si
toca el esquema, `.env.example` actualizado si agrega config, y documentación tocada si
cambia una decisión.

## Autenticación y roles

JWT de acceso corto + refresh largo (el vendedor no puede estar logueándose en terreno).

| Rol | Puede |
|---|---|
| `vendedor` | Ver catálogo, crear pedidos de **sus** clientes, registrar pagos |
| `cliente` | Ver catálogo con **su** lista de precios, crear sus pedidos, ver estado |
| `bodega` | Ver pedidos confirmados, marcar preparación y despacho |
| `admin` | Todo, incluye bandeja de conciliación y gestión de precios |

## Integraciones externas (adaptadores)

| Integración | Proveedor previsto | Interfaz | Fase |
|---|---|---|---|
| Cartola bancaria | Fintoc / Floid | `ProveedorCartola` | 2 |
| Pago en línea | Fintoc Pagos / Khipu / Webpay | `ProveedorPago` | 2 |
| DTE (SII) | SimpleAPI / LibreDTE / OpenFactura | `ProveedorDTE` | 2 |
| Mensajería | WhatsApp Business API | `ProveedorMensajeria` | 1 |

Cada uno tiene un `Fake*` en `tests/fakes/` usado por defecto en desarrollo, para poder
levantar el sistema completo sin credenciales de terceros.
