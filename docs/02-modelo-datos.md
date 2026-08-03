# Modelo de datos

Alcance: Fases 0 y 1. Las tablas de Fases 2–4 están esbozadas al final.

## Convenciones

- PK: `id` `BIGSERIAL`. Las entidades expuestas hacia clientes/app llevan además un
  `uuid` público (no exponer IDs secuenciales).
- Dinero: `NUMERIC(12,0)` en CLP, **sin decimales**. Sufijo `_clp`.
- Timestamps: `TIMESTAMPTZ`, `creado_en` / `actualizado_en` en toda tabla.
- Soft delete: `activo BOOLEAN` o estado. Nunca `DELETE` en pedidos/pagos.

## Fase 0 — Catálogo

### `catalogo_fuente`
Cada corrida de extracción. Permite versionar y comparar ediciones del catálogo.

| Campo | Tipo | Notas |
|---|---|---|
| `id` | bigserial | |
| `archivo` | text | Nombre del PDF |
| `sha256` | text | Hash del PDF, evita reprocesar |
| `edicion` | text | Ej. "10 JULIO 2026" |
| `paginas` | int | |
| `extraido_en` | timestamptz | |

### `catalogo_fila_cruda`
Salida literal del extractor, **antes** de normalizar. Es la red de seguridad: si el
parseo estuvo mal, se re-normaliza sin volver a tocar el PDF.

| Campo | Tipo | Notas |
|---|---|---|
| `id` | bigserial | |
| `fuente_id` | fk | |
| `pagina` | int | |
| `orden` | int | Orden vertical en la página |
| `codigo_raw` | text | `PR/49573`, `080633000-T`, … |
| `descripcion_raw` | text | Puede venir de fila combinada |
| `venta_min_raw` | text | `X 12 UNID`, `BOLSA X200UN` |
| `marca_raw` | text | Frecuentemente vacío |
| `medida_raw` | text | `1/2"`, `75W/80`, `12 LT` |
| `precio_raw` | text | `1.790` |
| `bbox` | jsonb | Posición, para depurar |
| `confianza` | numeric(3,2) | Heurística del extractor |
| `problemas` | text[] | Ej. `{precio_ilegible, sin_codigo}` |

### `producto`

| Campo | Tipo | Notas |
|---|---|---|
| `id` | bigserial | |
| `uuid` | uuid | Público |
| `sku` | citext unique | **SKU interno DVU**, generado |
| `descripcion` | text | |
| `categoria_id` | fk nullable | |
| `marca` | text nullable | |
| `medida` | text nullable | Texto original normalizado |
| `medida_valor` | numeric nullable | Parseado cuando se puede (para ordenar/filtrar) |
| `medida_unidad` | text nullable | `mm`, `pulg`, `lt`, … |
| `unidad_venta` | text | `UNID`, `BOLSA`, `CAJA` |
| `multiplo_venta` | int | **Obligatorio, default 1.** El carrito valida contra esto |
| `precio_lista_clp` | numeric(12,0) | Precio de referencia |
| `imagen_key` | text nullable | Key en MinIO |
| `activo` | bool | |

Índices: `pg_trgm` sobre `descripcion` para búsqueda difusa (el vendedor escribe "codo
1/2", no el SKU).

### `producto_alias`
Los códigos de proveedor del catálogo. Un producto puede tener varios.

| Campo | Tipo | Notas |
|---|---|---|
| `producto_id` | fk | |
| `codigo` | citext | `PR/49573` |
| `origen` | text | `catalogo_2026_07`, `proveedor_x` |

Unique `(codigo, origen)`. Índice sobre `codigo` — el vendedor busca por el código que
tenga a mano, sea el que sea.

### `categoria`
Árbol simple (`parent_id`). Se construye en Fase 0 asistido por LLM sobre las
descripciones, **con revisión humana** antes de publicar.

## Fase 1 — Operación

### `usuario`
`id`, `uuid`, `email` (citext unique), `password_hash`, `nombre`, `rol`
(`vendedor|cliente|bodega|admin`), `activo`.

### `cliente`
La ferretería que compra.

| Campo | Tipo | Notas |
|---|---|---|
| `rut` | citext unique | Normalizado `76123456-7`, validado módulo 11 |
| `razon_social` | text | |
| `nombre_fantasia` | text nullable | |
| `giro` | text | Requerido para la factura |
| `direccion`, `comuna`, `ciudad` | text | |
| `email_dte` | text | Dónde llega la factura |
| `telefono` | text | |
| `vendedor_id` | fk usuario nullable | Cartera |
| `condicion_pago` | text | `contado`, `credito_30`, … |
| `activo` | bool | |

### `pedido`

| Campo | Tipo | Notas |
|---|---|---|
| `id` | bigserial | |
| `uuid` | uuid | Público |
| `client_uuid` | uuid unique | **Generado por la app offline. Clave de idempotencia** |
| `numero` | text unique | Correlativo legible, `P-2026-00123` |
| `cliente_id` | fk | |
| `vendedor_id` | fk nullable | Null si el cliente pidió por la web |
| `origen` | text | `app_vendedor`, `web_cliente`, `admin` |
| `estado` | text | Ver máquina de estados |
| `neto_clp`, `iva_clp`, `total_clp` | numeric(12,0) | |
| `observaciones` | text | |
| `creado_en_dispositivo` | timestamptz | Cuándo lo creó el vendedor (≠ cuándo sincronizó) |
| `sincronizado_en` | timestamptz | |

### `pedido_linea`
`pedido_id`, `producto_id`, `sku` y `descripcion` **congelados** (el precio y el nombre
del producto pueden cambiar; el pedido no), `cantidad`, `multiplo_venta` congelado,
`precio_unitario_clp`, `total_linea_clp`.

> Congelar el precio y la descripción en la línea no es redundancia: es lo que hace que
> un pedido de hace 6 meses siga siendo legible y auditable.

### `pedido_evento`
Bitácora de la máquina de estados: `pedido_id`, `estado_anterior`, `estado_nuevo`,
`usuario_id`, `motivo`, `creado_en`. Es la fuente del seguimiento que ve el cliente.

### Máquina de estados del pedido

```
borrador ──> enviado ──> confirmado ──> preparacion ──> despachado ──> entregado ──> cerrado
                │            │              │               │
                └────────────┴──────────────┴───────────────┴──> anulado
```

Reglas:
- `borrador` solo existe en el dispositivo del vendedor; llega al backend como `enviado`.
- `confirmado` requiere validación de stock y de crédito del cliente.
- `despachado` requiere guía de despacho electrónica emitida (Fase 2).
- `entregado` requiere prueba de entrega (Fase 4).
- Transición inválida = error, no se corrige en silencio.

### `pago`

| Campo | Tipo | Notas |
|---|---|---|
| `uuid` | uuid | |
| `cliente_id` | fk | |
| `monto_clp` | numeric(12,0) | |
| `fecha_pago` | date | La que declara quien paga |
| `metodo` | text | `transferencia`, `efectivo`, `webpay`, … |
| `referencia` | text | N° de operación / glosa |
| `comprobante_key` | text nullable | Foto del comprobante en MinIO |
| `estado` | text | `declarado` → `verificado` / `rechazado` / `pendiente_revision` |
| `registrado_por` | fk usuario | El vendedor que lo subió |
| `verificado_por` | fk usuario nullable | |
| `movimiento_banco_id` | fk nullable **unique** | El abono que lo respalda |
| `conciliacion_confianza` | numeric(4,3) nullable | Puntaje con que lo aceptó la máquina; null si lo decidió una persona |

Un pago se aplica a uno o varios pedidos: tabla `pago_aplicacion`
(`pago_id`, `pedido_id`, `monto_clp`). Esto es lo que permite manejar el caso real de
"el cliente transfirió una vez para pagar tres facturas".

## Fase 2

### `movimiento_banco`

Cartola normalizada del agregador.

| Campo | Tipo | Notas |
|---|---|---|
| `id_externo` | text **unique** | Id del agregador. Es lo que hace **idempotente** resincronizar el mismo rango |
| `proveedor` | text | `fake`, `fintoc` |
| `fecha` | date | |
| `monto_clp` | numeric(12,0) | Los cargos van negativos y **nunca** concilian un pago |
| `descripcion` | text | Glosa del banco; suele traer el RUT de quien transfirió |
| `referencia` | text nullable | N° de operación |
| `rut_contraparte` | text nullable | Cuando el agregador lo entrega aparte |
| `estado` | text | `sin_conciliar` / `conciliado` / `ignorado` |

`pago.movimiento_banco_id` es **único**: un abono no puede respaldar dos comprobantes.
Nada se borra — un abono que no es de un cliente se marca `ignorado` y sigue auditable.

### `dte`

| Campo | Tipo | Notas |
|---|---|---|
| `tipo` | int | 33 factura afecta, 52 guía de despacho, 61 nota de crédito |
| `folio` | bigint nullable | Lo asigna el proveedor desde el CAF. Único junto con `tipo` |
| `pedido_id`, `cliente_id` | fk | |
| `rut_receptor` | text | Congelado: si el cliente cambia de razón social, el DTE emitido no cambia |
| `neto_clp`, `iva_clp`, `total_clp` | numeric(12,0) | Copiados del pedido, no recalculados |
| `estado` | text | `emitido` → `aceptado` / `rechazado` / `anulado` |
| `track_id` | text nullable | Id del envío al SII, para consultar después |
| `xml_key`, `pdf_key` | text nullable | Documentos en MinIO |
| `referencia_dte_id` | fk self nullable | La factura que corrige una nota de crédito |

Corregir una factura no es editarla ni borrarla: se emite una nota de crédito que la
referencia y la factura queda `anulado`. Las dos filas se conservan.

## Fases 3–4 (esbozo)

- `lista_precio` / `lista_precio_item` — precios por cliente o segmento.
- `despacho`, `despacho_parada`, `prueba_entrega` — logística y POD.
- `stock` / `movimiento_stock` — cuando exista fuente confiable de inventario.
