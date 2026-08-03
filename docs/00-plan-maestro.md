# Plan maestro — Proyecto DVU

> Versión 1 · 3 de agosto de 2026

## 1. Situación actual

Comercial DVU SpA distribuye productos ferreteros y de construcción a ferreterías
(B2B). Opera con vendedores en terreno y un catálogo PDF.

### Flujo actual

```
Vendedor visita ferretería
   └─> toma pedido en papel / cabeza
        └─> lo sube por WhatsApp al grupo
             └─> alguien lo interpreta y prepara el despacho
                  └─> cliente transfiere a la cuenta del dueño
                       └─> el dueño revisa la cartola A MANO
                            └─> alguien transcribe venta y pago a un EXCEL
```

### Dolores identificados

| # | Dolor | Costo |
|---|---|---|
| D1 | Pedidos por WhatsApp: sin formato, se pierden, se malinterpretan | Errores de despacho, reclamos |
| D2 | Verificación manual de pagos en la cartola | Horas diarias del dueño; no escala |
| D3 | Transcripción manual al Excel | Error humano, desfase, dato no confiable |
| D4 | Catálogo en PDF de 150 páginas | Desactualizado apenas se imprime; imposible de buscar |
| D5 | Cero trazabilidad del pedido | "¿Dónde está mi despacho?" se responde llamando |
| D6 | El conocimiento vive en el vendedor | Si se va, se va la cartera |

## 2. Análisis del catálogo (insumo real)

Fuente: `catalago/CAT ACT 10 JULIO 2026 PARTE {1,2}.pdf`

| Dato | Valor |
|---|---|
| Páginas | 150 (75 + 75), A4 |
| Origen | CorelDRAW 2025 (el PDF es un derivado, no la fuente de datos) |
| Filas con precio | ~2.200 |
| Texto | Extraíble (no es escaneo) |
| Imágenes | JPEG embebidos ~300 ppi, extraíbles |

Columnas: `Código | Imagen | Descripción | Detalle Venta Min | Marca | Medida | Precio`

### Hallazgos que condicionan el diseño

1. **Sin codificación unificada.** Al menos 5 formatos de código conviviendo
   (`PR/49573`, `080633000-T`, `ASK11003`, `KM521`, `FERCADGAL 174`, `1801205`).
   → Se requiere SKU interno + tabla de alias.
2. **`Venta Min` es el corazón del negocio B2B.** `X 12 UNID`, `X 20 UN`,
   `BOLSA X200UN`. No se venden unidades sueltas.
   → El modelo de datos y el carrito se construyen alrededor de esto.
3. **`Marca` casi siempre vacía; `Medida` heterogénea** (`1/2"`, `75W/80`, `12 LT`,
   `350X8`, `3 MM`). → Normalización semi-manual para filtros facetados.
4. **No hay stock, EAN ni categorías.** → Taxonomía a construir (asistida por LLM sobre
   las descripciones + revisión humana).
5. **El PDF no es la fuente de verdad.** Hay que determinar dónde vive hoy la lista de
   precios (ver §7, preguntas abiertas).

## 3. Sistema objetivo

```
┌─────────────┐  ┌─────────────┐  ┌──────────────┐
│  Web B2B    │  │  APK        │  │  Panel       │
│  (cliente)  │  │  Vendedor   │  │  Admin/Dueño │
└──────┬──────┘  └──────┬──────┘  └──────┬───────┘
       └────────────────┼────────────────┘
                   ┌────▼─────┐
                   │   API    │
                   └────┬─────┘
    ┌───────────┬───────┼────────┬──────────────┐
┌───▼────┐ ┌────▼───┐ ┌─▼─────┐ ┌▼──────────┐ ┌▼────────┐
│Catálogo│ │Pedidos │ │Pagos +│ │DTE / SII  │ │Despacho │
│Precios │ │Estados │ │Concil.│ │Fact+Guía  │ │+ POD    │
│Stock   │ │        │ │       │ │           │ │         │
└────────┘ └────────┘ └───────┘ └───────────┘ └─────────┘
```

## 4. Fases

### Fase 0 — Catálogo estructurado ← **EN CURSO**

Convertir los PDF en una base de datos de productos utilizable.

- [x] Análisis de estructura de los PDF
- [ ] Extractor posicional (pdfplumber) → filas normalizadas
- [ ] Extracción de imágenes de producto (PyMuPDF) → MinIO
- [ ] Normalización: `venta_minima` → `multiplo_venta` + `unidad`
- [ ] SKU interno + tabla de alias por código de proveedor
- [ ] Taxonomía de categorías (asistida, con revisión humana)
- [ ] Reporte de calidad: filas no parseadas, campos faltantes

**Criterio de salida:** ≥95% de las ~2.200 filas cargadas con código, descripción y
precio válidos; el 5% restante listado explícitamente para revisión manual.

### Fase 1 — Automatizar al vendedor ← **EN CURSO (backend)**

Ataca D1, D2, D3. Es la fase de mayor ROI: no depende de que los clientes cambien de
hábito.

- Backend: clientes, productos, pedidos, pagos, usuarios/roles
- App Android (Flutter) offline-first: catálogo, toma de pedido, registro de pago con
  foto del comprobante
- Panel admin: pedidos entrantes, bandeja de pagos por verificar
- **Exportación automática del mismo Excel que el dueño ya usa** (estrategia de
  transición: el sistema alimenta el Excel en vez de amenazarlo)
- Notificaciones de estado por WhatsApp Business API

**Criterio de salida:** 100% de los pedidos de al menos un vendedor entran por la app
durante 2 semanas seguidas, sin WhatsApp.

### Fase 2 — Pagos y tributación

- ✅ Conciliación bancaria vía agregador (Fintoc) + matching automático
- ✅ Bandeja de excepciones para lo que no matchea
- ✅ DTE al SII: factura tipo 33, nota de crédito 61, guía de despacho 52
- ⬜ Pago en línea desde el sistema (Fintoc Pagos / Khipu / Webpay)

**Criterio de salida:** ≥85% de los pagos conciliados sin intervención humana.

El umbral de aplicación automática es 0,85 y el monto exacto es requisito duro. El
puntaje con que se aceptó cada pago queda en `pago.conciliacion_confianza`, justamente
para poder medir ese criterio contra datos reales y recalibrar los pesos: hoy están
puestos sobre supuestos (que la glosa del banco suele traer el RUT y que los vendedores
rara vez anotan el nº de operación), no sobre una cartola de DVU.

Un empate nunca se resuelve solo. Si dos ferreterías transfirieron el mismo monto el
mismo día sin referencia, los dos pagos van a la bandeja: elegir sería inventar.

### Fase 3 — Ecommerce B2B

- Portal de cliente: login, catálogo con precios propios, recompra rápida
- Listas de precio por cliente/segmento
- Carga masiva de pedido por CSV/código

### Fase 4 — Despacho

- Ruteo, app de repartidor, prueba de entrega (foto + firma), tracking al cliente

## 5. Por qué este orden

El ecommerce **no** va primero, aunque sea lo más visible:

- Sin catálogo limpio ni stock confiable, un ecommerce muestra datos falsos.
- Los clientes ferreteros no migran de WhatsApp por decreto; necesitan una razón
  (precio visible, stock real, historial) que solo existe si el back está ordenado.
- Automatizar al vendedor produce los datos y la disciplina de proceso que el ecommerce
  requiere después, y genera ahorro desde el primer mes.

## 6. Riesgos

| Riesgo | Mitigación |
|---|---|
| Los vendedores vuelven a WhatsApp si la app agrega fricción | La app debe ser **más rápida** que WhatsApp para tomar un pedido. Medirlo. Integrar WhatsApp para notificaciones en vez de prohibirlo |
| El dueño no quiere conectar su cuenta bancaria a un tercero | Plan B: carga manual de cartola (CSV/OFX) con el mismo motor de matching. Un paso manual al día en vez de horas |
| Open Finance oficial (NCG 514) postergado a ~julio 2027 | No se depende de él. Se usa agregador ahora y se migra cuando exista |
| Datos de catálogo sucios (sin stock, sin categorías) | Fase 0 explícita con criterio de salida medible y reporte de calidad |
| El PDF deja de ser mantenido en Corel y nadie sabe generar el nuevo | El sistema **genera** el PDF del catálogo desde la BD (Fase 3) |

## 7. Preguntas abiertas (bloquean decisiones de diseño)

1. **¿Dónde vive hoy la lista de precios y el stock?** (Excel / ERP / POS / solo Corel)
   → Define si integramos o si el sistema pasa a ser la fuente de verdad.
2. **¿Los precios del catálogo son netos o con IVA?** ¿Hay listas por cliente/volumen?
3. **Volumen:** ¿cuántos vendedores, clientes activos y pedidos por día?
4. **¿DVU ya emite factura electrónica? ¿Con qué proveedor?**
5. **¿El dueño acepta conectar la cuenta bancaria a un agregador tipo Fintoc?**

Mientras no haya respuesta, el sistema asume: precios **netos** (IVA se calcula al
facturar), **una sola lista de precios**, y **sin integración ERP** (el sistema es la
fuente de verdad). Estos supuestos están aislados para poder cambiarlos.

## 8. Marcos de referencia

Categoría de producto: **B2B eCommerce + SFA (Sales Force Automation) / DMS**.

- **Pepperi** — el más cercano al caso: storefront B2B + app de toma de pedidos offline,
  listas de precio por cliente, route accounting. Útil como especificación funcional.
- **OroCommerce** — open source para distribuidores/mayoristas; su modelo de múltiples
  listas de precio es exactamente el problema de una distribuidora ferretera.
- **Shopify B2B/Wholesale** (absorbió Handshake) — rápido, pero se queda corto en venta
  por caja, DTE chileno y app de vendedor.
- **Portales del rubro en Chile:** Sodimac Constructor, Construmart, MTS, Prodalam,
  Dartel. Estudiar sobre todo la **recompra rápida** — en ferretería el grueso de los
  pedidos es reposición de lo mismo.
