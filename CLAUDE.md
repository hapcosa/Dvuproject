# CLAUDE.md — Proyecto DVU

Contexto e instrucciones para agentes trabajando en este repo.

## Qué es DVU

**Comercial DVU SpA** es una distribuidora mayorista chilena de productos ferreteros y de
construcción. Vende **B2B**: sus clientes son otras ferreterías, no consumidor final.

**Operación actual (la que estamos reemplazando):**

1. Vendedores en terreno visitan ferreterías con un catálogo PDF de 150 páginas.
2. Toman el pedido y lo suben **por WhatsApp**.
3. El cliente paga por transferencia a la cuenta del dueño de DVU.
4. El dueño **verifica el pago a mano** mirando la cartola del banco.
5. Alguien transcribe ventas y pagos a un **Excel**.

**Sistema objetivo:** ecommerce B2B + app Android para vendedores + conciliación bancaria
automática + DTE al SII + seguimiento del pedido hasta la entrega.

El plan completo por fases está en [`docs/00-plan-maestro.md`](docs/00-plan-maestro.md).
Lo que falta y no es código —credenciales, trámites, decisiones del negocio— está en
[`docs/03-puesta-en-marcha.md`](docs/03-puesta-en-marcha.md) (para quien opera el repo) y
[`docs/04-que-necesitamos-de-dvu.md`](docs/04-que-necesitamos-de-dvu.md) (para los dueños).
**Estado actual:** Fase 0 (extractor de catálogo), Fase 1 (backend de pedidos/pagos;
falta la app Flutter) y Fase 2 (conciliación bancaria y DTE; falta el pago en línea).
Más un **prototipo web** para mostrar: catálogo editable por el administrador y la
página donde el vendedor carga lo que hoy manda por WhatsApp
([`docs/05-catalogo-web.md`](docs/05-catalogo-web.md)).

## Reglas de dominio que NO se pueden violar

Estas salieron del análisis del catálogo real. Romperlas produce un sistema inservible.

1. **Venta por múltiplos, no por unidad.** Cada SKU tiene `venta_minima` (`X 12 UNID`,
   `BOLSA X200UN`). El carrito **siempre** valida `cantidad % multiplo_venta == 0`.
   Nunca asumas venta unitaria.
2. **Precios en CLP sin decimales.** Se guardan como **enteros** (`Numeric(12,0)` /
   `int`). Nunca `float` para dinero, en ninguna capa.
3. **Los códigos del catálogo son de proveedor, no de DVU.** Conviven `PR/49573`,
   `080633000-T`, `ASK11003`, `KM521`, `FERCADGAL 174`. El sistema tiene su propio
   `sku` interno y guarda los códigos originales como **alias** (`producto_alias`).
4. **Todo cliente necesita factura electrónica (DTE tipo 33).** Son empresas y
   descuentan IVA. La boleta no sirve. El despacho requiere **guía de despacho
   electrónica**.
5. **La conciliación de pagos nunca es 100% automática.** Siempre existe una bandeja de
   excepciones humana. Un pago que no matchea no se descarta: queda `pendiente_revision`.
6. **La app del vendedor es offline-first.** Se usa en bodegas y obras sin señal. Todo
   pedido se crea local con `client_uuid` y se sincroniza después; el backend debe ser
   **idempotente** frente a reenvíos.

## Stack

| Capa | Tecnología | Por qué |
|---|---|---|
| Lenguaje backend | Python 3.12 | El extractor de PDF manda; ecosistema maduro |
| API | FastAPI + Pydantic v2 | Tipado, OpenAPI gratis |
| ORM / migraciones | SQLAlchemy 2.0 + Alembic | Estándar |
| BD | PostgreSQL 16 | Transaccional, `citext`, `pg_trgm` para búsqueda |
| Jobs async | Redis + arq | Extracción, conciliación, DTE |
| Objetos | MinIO (S3-compatible) | Imágenes de productos, comprobantes de pago |
| Extracción PDF | pdfplumber (texto posicional) + PyMuPDF (imágenes) | |
| Contenedores | Docker + Docker Compose | **Sin Kubernetes** (decisión explícita, ver ADR-0002) |
| Lint / format | ruff | |
| Tipos | mypy (strict en `domain/`) | |
| Tests | pytest + testcontainers | |
| CI | GitHub Actions | |

Pendientes de fases posteriores: web Next.js (Fase 3), app Flutter (Fase 1b).

## Layout del repo

```
catalago/            PDFs fuente del catálogo (NO versionar, son ~380 MB)
docs/                Plan, arquitectura, modelo de datos, ADRs
backend/
  src/dvu/
    api/             FastAPI: routers, deps, schemas
    domain/          Lógica de negocio pura (sin I/O, mypy strict)
    db/              Modelos SQLAlchemy, sesión, repositorios
    extractor/       Fase 0: PDF -> filas normalizadas
    integraciones/   Terceros (banco, SII) detrás de un Protocol, con proveedor fake
    carga/           JSONL -> base de datos; datos de ejemplo; exportadores a Excel
    web/             Prototipo web: plantillas Jinja + CSS/JS sin build
    workers/         Jobs arq
  alembic/           Migraciones
  tests/
infra/               Config de servicios (postgres init, etc.)
scripts/             Utilidades de operación
```

## Comandos

Todo pasa por el `Makefile`. Nunca ejecutes `docker compose` a mano si hay un target.

```bash
make up            # levanta el stack completo
make down
make logs
make shell         # shell en el contenedor api
make test          # pytest
make lint          # ruff + mypy
make fmt           # ruff format
make migrate       # alembic upgrade head
make revision m="..."   # nueva migración
make extract       # corre el extractor sobre catalago/*.pdf
make cargar-catalogo  # carga el JSONL extraído a la BD (idempotente)
make seed          # usuarios y clientes de ejemplo (nunca en producción)
make exportar      # genera el Excel de ventas en data/exports/
make conciliar     # trae la cartola del banco y concilia los pagos declarados
make cartola-demo  # cartola de prueba para ensayar la conciliación sin agregador
```

## Convenciones

- **Idioma:** el dominio se nombra en **español** (`Pedido`, `Cliente`, `venta_minima`)
  porque es el idioma del negocio y evita traducciones ambiguas. La infraestructura
  técnica va en inglés (`get_session`, `settings`, `router`).
- **Dinero:** enteros CLP en todas las capas. Sufijo `_clp` en los campos.
- **Migraciones:** una por PR. Siempre reversible (`downgrade` implementado).
- **Secretos:** nunca en el repo. `.env.example` documenta las variables; `.env` está
  en `.gitignore`.
- **Commits:** convencionales (`feat:`, `fix:`, `docs:`, `chore:`).

## Trampas conocidas

- Los PDF del catálogo pesan ~380 MB. **Están en `.gitignore`.** No los agregues a git.
- `pdftotext` sin `-layout` mezcla columnas y produce basura. El extractor usa
  posiciones de palabras (pdfplumber), no texto plano.
- Las filas del catálogo tienen **descripciones combinadas**: una descripción abarca
  varias filas de código/medida/precio. El extractor propaga hacia abajo (`forward fill`)
  dentro del bloque visual.
- Muchas filas tienen `Marca` vacía y `Venta Min` vacía. Son datos faltantes reales, no
  errores de parseo. No los inventes.
- Chile: RUT con dígito verificador. Validar siempre con módulo 11, guardar normalizado
  sin puntos y con guion (`76123456-7`).
- **Los tests borran el esquema entero** (`Base.metadata.drop_all`, al empezar y al
  terminar). `conftest.py` le pega el sufijo `_test` a la base de `DVU_DATABASE_URL`
  justamente por eso: sin ese sufijo, `make test-all` con el stack levantado se lleva
  puesto el catálogo cargado sin preguntar y sin aviso —el drop es lo último que corre,
  después de que pytest ya imprimió que todo pasó—. **Pasó.** No apuntes los tests a la
  base del stack ni saques el sufijo. Para recuperar: `~/backups/migracion-prod/dvu.dump`.
- El **signo `$` es una palabra suelta** que cae justo sobre la frontera
  `medida`/`precio` (centro 536.9–547.9 pt, frontera en 545.0). Clasificado por posición
  se pegaba a la medida en el 76% de los casos: 231 filas quedaron con `medida = "$"`.
  Por eso `columna_de_palabra()` clasifica **por contenido** los tokens que son sólo
  signos peso. En algunas páginas viene duplicado (`"$$"`): los regex de precio aceptan
  `\$*`, no `\$?`. Mover la frontera X no sirve — las medidas legítimas llegan a 532.3 y
  el margen es de 4 pt.

## Infraestructura: dónde corre esto

**Producción = `10.244.117.161`** (migrado el 2026-08-14). El host anterior
`10.244.19.205` (`traderbot`) pasó a ser el entorno de **test**.

- El repo vive en `~/servicios/Dvuproject`. Postgres y MinIO usan **volúmenes
  Docker con nombre** (`dvu_pgdata`, `dvu_miniodata`, `dvu_redisdata`), no bind
  mounts: para migrarlos hay que hacer `pg_dump` y un `tar` del volumen, no
  basta con copiar un directorio.
- **Puerto host de Postgres: `5435`**, no el 5432. En este PC el 5432 lo ocupa
  el Postgres de signalsTrading, que volvió al puerto canónico cuando murió su
  stack legacy. Se cambia con `POSTGRES_PORT_HOST` en el `.env`.
- `docker-compose.override.yml` es infraestructura de la máquina y no vive en
  el repo.
