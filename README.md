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
producción) y readiness en `/health/ready`. En la misma dirección está el
[prototipo web](#prototipo-web): catálogo en `/`, carga de comprobantes en `/vendedor`.

Todas las integraciones externas —banco, pagos, DTE, WhatsApp— vienen en modo `fake` por
defecto, así que el stack levanta completo sin credenciales de terceros.

## Cargar el catálogo

```bash
make extract              # catalago/*.pdf -> data/extraccion/*.jsonl + fotos + reporte
make cargar-catalogo      # data/extraccion -> producto, producto_alias, fotos al almacén
make clasificar           # arma el árbol de categorías y clasifica por descripción
```

`make extract` deja esto en `data/extraccion/`:

| Archivo | Contenido |
|---|---|
| `catalogo.jsonl` | Filas cargables (código + descripción + precio) |
| `revision.jsonl` | Filas que necesitan intervención humana, con su diagnóstico |
| `reporte.json` | Métricas de calidad y verificación del criterio de Fase 0 |
| `fuentes.json` | sha256 y páginas de cada PDF, para trazar la carga |
| `imagenes/` + `imagenes.json` | Las fotos de producto y a qué fila va cada una |

Las fotos salen del PDF por posición: se toma lo que cae en la columna «Imagen», se
descartan iconos y logos por tamaño, y se deduplica por sha256 —una misma foto sirve a
toda una familia de productos, no a un SKU—. `cargar-catalogo --con-imagenes` las sube
al almacén y les pone el `imagen_key` al producto; sin ese flag la carga es sólo texto,
porque extraer las imágenes es una corrida mucho más lenta y cargar los precios no puede
depender de ella. Una foto que falte en disco no aborta la carga: esa fila queda sin
imagen y el resto entra igual.

### Categorías

**El PDF no las trae**: sus páginas sólo tienen el folio y los títulos de columna, así
que no hay encabezado de sección que extraer. El árbol se define a mano en
[`dvu/domain/categorias.py`](backend/src/dvu/domain/categorias.py) y `make clasificar` lo
aplica sobre la descripción con palabras clave explícitas. Última corrida sobre el
catálogo real: **73 % clasificado** (1.448 de 1.975 productos) en diez categorías.

Lo que ninguna regla reconoce **queda sin categoría a propósito** y se lista en la
salida del comando. Una categoría inventada es peor que ninguna: el vendedor navega el
árbol, no encuentra lo que sabe que existe y deja de confiar en el árbol completo. Esos
productos se siguen encontrando por búsqueda de texto, que es como se busca hoy, y en
`/admin` hay un filtro «Sin categoría» que es exactamente la lista de revisión.

La clasificación automática **no pisa lo que asignó una persona**: sólo toca los
productos sin categoría. `--reclasificar` fuerza la corrida completa y existe para
cuando cambian las reglas; es explícito porque destruye correcciones.

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
| `GET` | `/productos` | cualquiera | Catálogo con búsqueda por texto y alias; filtra por `categoria` o `sin_categoria` |
| `GET` | `/categorias` | cualquiera | Árbol con el conteo de productos; las vacías no se ofrecen |
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
| `POST` | `/productos` | admin | Alta manual de lo que no viene en el PDF |
| `PATCH` | `/productos/{sku}` | admin | Corrige la ficha; `activo=false` la desactiva |
| `POST` | `/productos/{sku}/alias` | admin | Suma un código de proveedor |
| `POST` | `/categorias` | admin | Crea una categoría; slug repetido da 409 |
| `PATCH` | `/categorias/{slug}` | admin | Renombra o reordena; el slug no se toca |
| `POST` | `/productos/{sku}/imagen` | admin | Reemplaza la foto; sólo JPEG, PNG o WebP |
| `GET` | `/productos/{sku}/imagen` | cualquiera | Redirige a una URL firmada de vida corta |
| `POST` | `/comprobantes` | vendedor | Registra la transferencia avisada; nunca rechaza |
| `POST` | `/comprobantes/{uuid}/imagen` | vendedor | Adjunta la foto del comprobante |
| `GET` | `/comprobantes` | cualquiera | Bandeja de cobranza; el vendedor sólo ve los suyos |
| `POST` | `/comprobantes/{uuid}/ingresado` | admin | Sale de la bandeja; la fila no se borra |
| `GET` | `/comprobantes/reporte.xlsx` | admin | El Excel de cobranza, con los colores del bot |

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

## Prototipo web

Cinco páginas servidas por la misma app, en <http://localhost:8000>. Reemplazan el
catálogo PDF y el grupo de WhatsApp «COMPROBANTES TRANSF.» — **no** cablean pagos en
línea ni despacho.

| Ruta | Quién | Para qué |
|---|---|---|
| `/` | cualquiera | El catálogo con el diseño del PDF impreso, buscador y filtro por categoría |
| `/pedido` | vendedor, cliente | Arma el pedido desde el catálogo y lo envía |
| `/vendedor` | vendedor | El formulario que reemplaza el mensaje de WhatsApp |
| `/cobranza` | admin | Bandeja de comprobantes + descarga del Excel |
| `/admin` | admin | Edita el catálogo celda por celda y cambia las fotos |

Son **clientes de la API JSON**: piden el token a `/auth/login` y desde ahí llaman a los
mismos endpoints documentados en `/docs`. No hay sesión de servidor ni datos incrustados
en las plantillas, así que hay un solo modelo de permisos que mantener. Sin framework,
sin build y sin CDN: se abren en cualquier navegador sin instalar nada.

El vendedor puede enviar un comprobante incompleto a propósito: se guarda marcado con lo
que falta (`FALTA MONTO`, `FALTA FACTURA`, …), con los mismos estados y colores que
cobranza ya lee en el Excel de hoy. Perder el aviso es peor que registrarlo a medias.

En `/pedido` las cantidades se escriben **en envases**, no en unidades: el vendedor pone
«2 cajas» y la página envía `2 × multiplo_venta`, así que la cantidad es múltiplo válido
por construcción y no hay forma de tipear un número que el backend vaya a rechazar. El
carrito vive en `sessionStorage` con un `client_uuid` que **no** se regenera entre
intentos: si se corta la señal al enviar, reintentar cae en la idempotencia del backend
en vez de duplicar el pedido. La página tampoco calcula IVA — muestra el neto y después
los totales que devolvió el servidor, porque la regla del impuesto vive en un solo lugar.

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
