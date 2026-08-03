# Proyecto DVU

Sistema de comercio B2B para **Comercial DVU SpA**, distribuidora mayorista chilena de
productos ferreteros y de construcción.

Reemplaza la operación actual —vendedores tomando pedidos por WhatsApp, pagos verificados
a mano en la cartola del banco y transcritos a un Excel— por: catálogo digital, app de
vendedores offline-first, ecommerce para las ferreterías clientes, conciliación bancaria
asistida y seguimiento del pedido hasta la entrega.

- **Plan por fases:** [`docs/00-plan-maestro.md`](docs/00-plan-maestro.md)
- **Arquitectura:** [`docs/01-arquitectura.md`](docs/01-arquitectura.md)
- **Modelo de datos:** [`docs/02-modelo-datos.md`](docs/02-modelo-datos.md)
- **Decisiones:** [`docs/adr/`](docs/adr/)
- **Contexto para agentes:** [`CLAUDE.md`](CLAUDE.md)

## Estado

| Fase | Alcance | Estado |
|---|---|---|
| 0 | Catálogo PDF → base de datos | Extractor y carga funcionando |
| 1 | Backend de pedidos y pagos + app del vendedor | API funcionando; falta la app Flutter |
| 2 | Conciliación bancaria + DTE al SII | Backend funcionando; falta pago en línea |
| 3 | Ecommerce web para clientes | Diseñada |
| 4 | Despacho y seguimiento | Diseñada |

**Fase 0 — criterio de salida:** ≥95 % de las filas del catálogo cargables sin
intervención manual. Última corrida sobre las 150 páginas reales: **97,23 %** (1.998 de
2.055 filas), con las 57 restantes listadas explícitamente en `revision.jsonl`.

## Requisitos

- Docker + Docker Compose (no se usa Kubernetes, ver [ADR-0002](docs/adr/0002-sin-kubernetes.md))
- `make`
- Para desarrollo fuera de contenedor: Python 3.12 y [uv](https://docs.astral.sh/uv/)

Los PDF del catálogo **no están versionados** (pesan ~380 MB). Déjalos en `catalago/`
antes de correr el extractor.

## Puesta en marcha

```bash
cp .env.example .env      # ajusta DVU_SECRET_KEY antes de cualquier despliegue
make init                 # construye imágenes y prepara volúmenes
make up                   # api, worker, postgres, redis, minio
make migrate              # alembic upgrade head
make seed                 # usuarios y clientes de ejemplo
```

La API queda en <http://localhost:8000>, con documentación en `/docs` (deshabilitada en
producción) y readiness en `/health/ready`.

Todas las integraciones externas —banco, pagos, DTE, WhatsApp— vienen en modo `fake` por
defecto, así que el stack levanta completo sin credenciales de terceros.

## Cargar el catálogo

```bash
make extract              # catalago/*.pdf -> data/extraccion/*.jsonl + reporte
make cargar-catalogo      # data/extraccion -> producto, producto_alias
```

`make extract` deja cuatro archivos en `data/extraccion/`:

| Archivo | Contenido |
|---|---|
| `catalogo.jsonl` | Filas cargables (código + descripción + precio) |
| `revision.jsonl` | Filas que necesitan intervención humana, con su diagnóstico |
| `reporte.json` | Métricas de calidad y verificación del criterio de Fase 0 |
| `fuentes.json` | sha256 y páginas de cada PDF, para trazar la carga |

La carga es **idempotente**: repetirla con el mismo JSONL deja la base igual. Nada se
borra —los productos ausentes de una edición se marcan inactivos con
`--desactivar-ausentes`— porque pueden estar referenciados en pedidos históricos.

## API (Fase 1)

Documentación interactiva en `http://localhost:8000/docs` (deshabilitada en producción).
Todo cuelga de `/api/v1`.

| Método | Ruta | Rol | Para qué |
|---|---|---|---|
| `POST` | `/auth/login` | — | Devuelve access + refresh token |
| `POST` | `/auth/refresh` | — | Renueva el access token |
| `GET` | `/auth/yo` | cualquiera | Usuario de la sesión |
| `GET` | `/productos` | cualquiera | Catálogo con búsqueda por texto y alias |
| `POST` | `/clientes` | vendedor | Alta de ferretería; valida el RUT por módulo 11 |
| `GET` | `/clientes` | cualquiera | El vendedor sólo ve su cartera |
| `GET` | `/clientes/{rut}` | cualquiera | Ficha del cliente |
| `PATCH` | `/clientes/{rut}` | vendedor | Corrige la ficha o la desactiva (`activo=false`) |
| `POST` | `/pedidos` | vendedor, cliente | Crea el pedido; idempotente por `client_uuid` |
| `GET` | `/pedidos` | cualquiera | El vendedor sólo ve los suyos |
| `GET` | `/pedidos/{numero}` | cualquiera | Detalle con líneas y bitácora |
| `POST` | `/pedidos/{numero}/estado` | bodega, admin | Avanza el pedido; anular exige motivo |
| `POST` | `/pagos` | vendedor, cliente | Declara el pago y lo aplica a pedidos |
| `POST` | `/pagos/{uuid}/comprobante` | vendedor, cliente | Sube la foto de la transferencia |
| `GET` | `/pagos/{uuid}/comprobante` | cualquiera | Redirige a una URL firmada de vida corta |
| `GET` | `/pagos` | cualquiera | Bandeja; `?estado=pendiente_revision` es la de excepciones |
| `POST` | `/pagos/{uuid}/estado` | admin | Verifica, rechaza o manda a revisión |
| `GET` | `/reportes/ventas.xlsx` | admin | El Excel del dueño, generado al vuelo |

Dos reglas que la API impone sin excepción: el pedido rechaza cualquier cantidad que no
sea múltiplo de la venta mínima (y responde con la `cantidad_sugerida` de **todas** las
líneas malas a la vez, porque reenviar cuesta señal), y un pago que no cuadra nunca se
descarta: queda `pendiente_revision`.

## Conciliación y DTE (Fase 2)

Reemplaza al dueño verificando transferencias a mano contra la cartola del banco.

| Método | Ruta | Rol | Para qué |
|---|---|---|---|
| `POST` | `/conciliacion/sincronizar` | admin | Trae la cartola y concilia lo que supera el umbral |
| `GET` | `/conciliacion/bandeja` | admin | Pagos y abonos sin cruzar, los dos lados |
| `POST` | `/conciliacion/aplicar` | admin | Confirma a mano un cruce de la bandeja |
| `GET` | `/conciliacion/movimientos` | admin | La cartola ya sincronizada |
| `POST` | `/conciliacion/movimientos/{id}/ignorar` | admin | Saca un abono que no es de un cliente |
| `POST` | `/dte/facturas` | admin | Factura afecta tipo 33 |
| `POST` | `/dte/guias` | admin | Guía de despacho tipo 52 |
| `POST` | `/dte/notas-credito` | admin | Nota de crédito tipo 61; exige motivo |
| `GET` | `/dte` | cualquiera | Documentos emitidos; el vendedor sólo los suyos |

El matching es por monto exacto (requisito duro) más evidencia: nº de operación en la
glosa, RUT de la contraparte y cercanía de fecha (±3 días, porque el banco acredita al
día siguiente). Sobre 0,85 se aplica solo y queda registrado con qué confianza; **un
empate nunca se resuelve automáticamente** — si dos ferreterías transfirieron lo mismo el
mismo día, decide una persona. Nada se descarta ni se borra.

Emitir es irreversible: un folio entregado al SII no se edita, se corrige con nota de
crédito. Y un pedido no pasa a `despachado` sin guía electrónica emitida.

Sin credenciales del agregador ni del proveedor de DTE el stack levanta igual, con los
adaptadores `fake`. Para ensayar la conciliación:

```bash
make cartola-demo   # cartola de prueba a partir de los pagos declarados
make conciliar
```

## Desarrollo

```bash
make check     # ruff check + ruff format --check + mypy   (lo mismo que corre CI)
make test      # pytest, sin los tests que necesitan los PDF
make test-all  # incluye los tests marcados `pdf`
make fmt
make logs
make shell
```

Los tests marcados `pdf` se saltan solos si `catalago/` está vacío, así que CI pasa sin
los originales.

## Operación

```bash
make conciliar           # cartola del banco → pagos verificados + bandeja
make exportar            # Excel de ventas a data/exports/
make backup              # dump a data/backups/
scripts/backup_db.sh     # lo mismo, con retención y verificación — pensado para cron
make deploy              # levanta sin el override de desarrollo
```

`DVU_ENV=production` activa una validación al arrancar que aborta el proceso si quedó la
`SECRET_KEY` de ejemplo, si `DVU_DEBUG=true` o si hay proveedores `fake` activos. Falla al
arrancar, no en la primera venta.

## Licencia

Software propietario de Comercial DVU SpA.
