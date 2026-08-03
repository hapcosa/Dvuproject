# Puesta en marcha — lo que tienes que hacer tú

> Guía operativa paso a paso. Versión 1 · 3 de agosto de 2026
>
> Este documento es para **ti** (quien opera el repo). Lo que hay que pedirle a los
> dueños de DVU está en [`04-que-necesitamos-de-dvu.md`](04-que-necesitamos-de-dvu.md).

El código de las fases 0, 1 y 2 está listo y mergeado. Lo que falta para que esto sea un
sistema en uso son cosas que **no se resuelven escribiendo código**: datos reales,
credenciales, trámites y decisiones del negocio.

Están ordenadas por dependencia. No saltes bloques: el bloque 3 no sirve si el 1 no está
hecho.

---

## Bloque 0 — Correr el sistema en tu máquina (30 min)

Objetivo: verlo funcionando completo, sin credenciales de nadie.

```bash
cd ~/programacion/ProyectoDvu

cp .env.example .env          # queda todo en modo `fake`; no necesitas cuentas de terceros
make init
make up                       # api, worker, postgres, redis, minio
make migrate                  # crea las tablas
make seed                     # 3 usuarios y 3 clientes de ejemplo

pre-commit install            # ← instala los hooks; sin esto NO se ejecutan
```

> `pre-commit install` es fácil de olvidar y es lo que activa el bloqueo de `.env`, el
> límite de tamaño que impide subir los PDF y el `ruff` automático. Están declarados en
> `.pre-commit-config.yaml`, pero un archivo de configuración no bloquea nada por sí solo.

Comprobaciones:

1. Abre <http://localhost:8000/docs> — debe listar los endpoints de las tres fases.
2. Abre <http://localhost:8000/health/ready> — debe responder `{"ready": true, ...}` con
   el estado de cada dependencia (base, Redis, MinIO). Si alguna está en `false`, ahí
   está el problema.
3. Loguéate en `/docs` con `POST /auth/login`:
   - usuario `admin@dvu.cl`, contraseña `dvu-dev-1234`
   - (también existen `vendedor@dvu.cl` y `bodega@dvu.cl`, misma contraseña)
   - copia el `access_token` en el botón **Authorize** de la parte superior.

Ensaya el ciclo completo desde `/docs`, en este orden:

| # | Endpoint | Qué estás probando |
|---|---|---|
| 1 | `POST /pedidos` | Que rechace una cantidad que no sea múltiplo de la venta mínima |
| 2 | `POST /pedidos/{numero}/estado` → `confirmado` | El pedido congela sus precios |
| 3 | `POST /dte/facturas` | Sale factura 33 con folio del proveedor `fake` |
| 4 | `POST /pedidos/{numero}/estado` → `despachado` | **Debe fallar con 409**: falta la guía |
| 5 | `POST /dte/guias` | Guía 52 |
| 6 | Repite el paso 4 | Ahora sí pasa |
| 7 | `POST /pagos` | Declara la transferencia |

Y después, la conciliación:

```bash
make cartola-demo   # inventa una cartola que calza (a medias) con los pagos declarados
make conciliar
```

Verás algo así: unos pagos conciliados solos, otros en la bandeja. **Eso es correcto y es
el punto**: la cartola de prueba mete a propósito desfases de fecha y referencias
faltantes, para que veas la bandeja de excepciones funcionando. Míralas con
`GET /conciliacion/bandeja` y crúzalas a mano con `POST /conciliacion/aplicar`.

> Si algo falla aquí, no sigas. Todo lo demás asume que esto corre.

---

## Bloque 1 — Cargar el catálogo real (1–2 horas + revisión manual)

Sin esto el sistema está vacío y no le puedes mostrar nada a nadie.

```bash
# 1. Deja los PDF en catalago/  (NO se versionan: pesan ~380 MB y están en .gitignore)
ls catalago/
# CAT ACT 10 JULIO 2026 PARTE 1.pdf
# CAT ACT 10 JULIO 2026 PARTE 2.pdf

make extract            # ~10 min con imágenes
make cargar-catalogo
```

`make extract` deja cuatro archivos en `data/extraccion/`:

| Archivo | Qué hacer con él |
|---|---|
| `catalogo.jsonl` | Se carga solo con `make cargar-catalogo` |
| `revision.jsonl` | **Requiere ojo humano.** Ver abajo |
| `reporte.json` | Léelo: dice si se cumplió el criterio de ≥95 % |
| `fuentes.json` | sha256 de cada PDF. No lo toques, es la trazabilidad |

### Lo que sí te va a costar trabajo: `revision.jsonl`

En la última corrida quedaron **57 filas** (2,77 %) que el extractor no pudo interpretar
solo. No las inventa a propósito: prefiere dejarlas fuera antes que cargar un precio
equivocado.

Pasos:

1. Abre `revision.jsonl`. Cada línea trae en `problemas` el diagnóstico de por qué no
   pasó, y en `confianza` cuánto le faltó para el umbral.
2. Ábrelo junto al PDF en la página que indica el campo `pagina`.
3. Corrige el valor a mano y muévelo a `catalogo.jsonl`, o descártalo si es basura del
   PDF (encabezados, notas al pie).
4. Vuelve a correr `make cargar-catalogo`. Es **idempotente**: repetirlo no duplica nada.

> Presupuesta una tarde. Son 57 filas, pero hay que mirar el PDF fila por fila.

### Lo que queda pendiente de Fase 0 (y necesita decisión, no solo trabajo)

- **Categorías.** Los ~2.000 productos no tienen taxonomía; el PDF no la trae. Sin
  categorías, el ecommerce de Fase 3 es una lista plana de 2.000 ítems: inusable.
  El plan es proponerlas con un LLM sobre las descripciones y que **alguien de DVU las
  revise** — no se cargan sin revisión humana.
- **Imágenes a MinIO.** El extractor ya las saca del PDF; falta subirlas al bucket y
  asociarlas al SKU.

---

## Bloque 2 — Conseguir las credenciales (depende de los dueños)

Estas son las cuatro llaves que apagan los modos `fake`. **Ninguna la puedes conseguir tú
solo**: todas requieren que el dueño de DVU firme, pague o autorice algo. Pídelas con el
documento [`04-que-necesitamos-de-dvu.md`](04-que-necesitamos-de-dvu.md).

| Integración | Variables en `.env` | Sin ella |
|---|---|---|
| Banco (cartola) | `DVU_BANCO_PROVEEDOR=fintoc`, `DVU_BANCO_API_KEY`, `DVU_BANCO_LINK_TOKEN`, `DVU_BANCO_CUENTA_ID` | La conciliación corre contra un archivo de mentira |
| DTE / SII | `DVU_DTE_PROVEEDOR=api`, `DVU_DTE_API_KEY`, `DVU_DTE_AMBIENTE` | Las facturas tienen folios inventados, no existen para el SII |
| WhatsApp | `DVU_WHATSAPP_PROVEEDOR=meta`, `DVU_WHATSAPP_TOKEN`, `DVU_WHATSAPP_PHONE_NUMBER_ID` | No hay avisos de estado al cliente |
| Pago en línea | `DVU_PAGOS_PROVEEDOR`, según el elegido | El cliente sigue transfiriendo a mano (hoy es así igual) |

### Al conectar el DTE, cuida este orden

1. **Primero certificación.** Deja `DVU_DTE_AMBIENTE=certificacion` y emite ahí todo lo
   que quieras: no existe para el fisco.
2. Completa los datos del emisor en `.env`. Con `DVU_EMISOR_DIRECCION` o
   `DVU_EMISOR_COMUNA` vacíos, **el SII rechaza el documento**. Hoy están vacíos.
3. Recién cuando el flujo completo funcione en certificación, cambia a `produccion`.
   A partir de ahí **cada emisión es irreversible**: un folio entregado al SII no se
   edita ni se borra, se corrige con una nota de crédito que queda registrada. No lo
   cambies "para probar".

### Cuando elijas proveedor de DTE, hay que tocar código

`backend/src/dvu/integraciones/dte.py` tiene el adaptador HTTP escrito, pero las URL en
`DteApi.BASES` son **un placeholder**: no corresponden a ningún proveedor real. Al elegir
(SimpleAPI, LibreDTE, OpenFactura u otro) hay que reemplazar:

- las URL base de certificación y producción,
- la forma del payload en `_a_payload()`,
- los nombres de campo que se leen de la respuesta en `emitir()` y `consultar()`.

La lógica de dominio —qué documento corresponde, cuándo, con qué referencias— no cambia.
Solo cambia el contrato con el proveedor.

---

## Bloque 3 — Calibrar la conciliación con datos reales (2 semanas)

Esto es lo más importante que queda y **nadie lo puede hacer sin la cartola real de DVU**.

El motor decide con un puntaje: monto exacto es obligatorio, y suman el nº de operación
en la glosa, el RUT de la contraparte y la cercanía de fecha. Sobre **0,85** aplica solo.

**Los pesos son supuestos míos, no medición.** Están puestos sobre dos creencias: que la
glosa del banco suele traer el RUT, y que los vendedores rara vez anotan el nº de
operación. Si en el banco de DVU eso no se cumple, el criterio de ≥85 % automático no se
alcanza — y no lo vamos a saber hasta ver una cartola de verdad.

Procedimiento:

1. Conecta el agregador y deja correr la conciliación dos semanas. El worker ya la
   dispara sola cada media hora entre 09:00 y 18:30.
2. Consulta cuánto se concilió solo y con qué confianza:

   ```sql
   SELECT
     count(*) FILTER (WHERE conciliacion_confianza IS NOT NULL) AS automaticos,
     count(*) FILTER (WHERE conciliacion_confianza IS NULL
                        AND movimiento_banco_id IS NOT NULL)   AS manuales,
     count(*) FILTER (WHERE movimiento_banco_id IS NULL)       AS sin_cruzar,
     avg(conciliacion_confianza)                               AS confianza_media
   FROM pago;
   ```

   (`conciliacion_confianza IS NULL` con movimiento asignado = lo cruzó una persona.)
3. Si `automaticos / total < 0,85`, mira los cruces que hizo una persona: ¿qué evidencia
   tenían que el motor no supo puntuar? Ahí está el peso que hay que mover.
4. Ajusta los pesos en `backend/src/dvu/domain/conciliacion.py` y actualiza el registro
   de la decisión en [`adr/0003-conciliacion-con-umbral.md`](adr/0003-conciliacion-con-umbral.md).

> **No subas el umbral para que el número se vea mejor.** El error de conciliar mal es
> mucho más caro que el de mandar un pago a la bandeja: dar por pagado un pedido que no
> se pagó despacha mercadería gratis. La bandeja solo cuesta un minuto de alguien.

---

## Bloque 4 — Antes de producción

Checklist. Todo lo que aparece aquí se verifica **antes** del primer pedido real.

- [ ] `DVU_SECRET_KEY` generada con `openssl rand -hex 32`. La de `.env.example` no sirve:
      el sistema **se niega a arrancar** con ella en producción.
- [ ] `DVU_DEBUG=false` y `DVU_ENV=production`.
- [ ] `POSTGRES_PASSWORD` y `MINIO_ROOT_PASSWORD` cambiadas (las de ejemplo son públicas).
- [ ] `DVU_CORS_ORIGINS` apuntando al dominio real, no a `localhost`.
- [ ] Ningún proveedor en `fake`. El arranque también lo valida para DTE y pagos.
- [ ] **Nunca correr `make seed` en producción.** Aborta solo, pero no lo pruebes.
- [ ] Backups automáticos: `scripts/backup_db.sh` en cron, con retención. Y **restaurar
      un backup en una máquina limpia al menos una vez** — un backup no probado no es un
      backup.
- [ ] `DVU_SENTRY_DSN` configurado, o algún otro modo de enterarte de los errores que no
      sea que te llame el dueño.
- [ ] Certificado TLS. La API maneja RUT, montos y datos bancarios de las ferreterías.
- [ ] Verificar que `.env` **no** está en git: `git check-ignore -v .env` debe responder.

Despliegue:

```bash
make deploy    # levanta sin el override de desarrollo y aplica migraciones
```

---

## Bloque 5 — Lo que falta construir

En orden de valor para el negocio:

| # | Qué | Por qué ahora | Tamaño |
|---|---|---|---|
| 1 | **App Flutter del vendedor** | Es el 80 % del ROI. Sin ella el backend no lo usa nadie: los pedidos siguen entrando por WhatsApp | Grande |
| 2 | **Panel admin web** | Hoy la bandeja de excepciones sólo se opera desde `/docs`. El dueño no va a usar Swagger | Medio |
| 3 | Categorías del catálogo | Bloquea la Fase 3 | Medio (mucha revisión humana) |
| 4 | Notificaciones WhatsApp | Quita las llamadas de "¿dónde está mi pedido?" | Chico |
| 5 | Pago en línea | Único ítem pendiente de Fase 2. Mejora la conciliación de raíz: un pago originado en el sistema ya viene identificado | Medio |
| 6 | Ecommerce B2B (Fase 3) | Después de que el back esté ordenado, no antes | Grande |

La app del vendedor es offline-first: se usa en bodegas y obras sin señal. El backend ya
es idempotente por `client_uuid`, así que reenviar el mismo pedido diez veces crea uno
solo. Eso ya está resuelto del lado servidor.

---

## Rutina de operación diaria (cuando esté en marcha)

Casi todo corre solo. Lo que requiere una persona:

| Cuándo | Qué | Cómo |
|---|---|---|
| Cada mañana | Revisar la bandeja de excepciones | `GET /conciliacion/bandeja` |
| Cada mañana | Pagos en `pendiente_revision` | `GET /pagos?estado=pendiente_revision` |
| Al despachar | Emitir la guía antes de que salga el camión | `POST /dte/guias` |
| Fin de mes | Excel de ventas | `make exportar` |
| Diario, automático | Backup | `scripts/backup_db.sh` en cron |

La conciliación no necesita que nadie la dispare: el worker corre cada media hora en
horario hábil. `make conciliar` es para forzarla.

---

## Cosas que no debes hacer

- **No borrar filas.** Ni pagos, ni DTE, ni productos, ni clientes. Todo se desactiva o
  se anula por estado. Un DTE borrado es un problema con el SII; un producto borrado
  rompe los pedidos históricos que lo referencian.
- **No editar una factura emitida.** Se corrige con nota de crédito. El sistema no te va
  a dejar, pero tampoco lo intentes por la base.
- **No versionar los PDF del catálogo** (~380 MB) ni el `.env`. Los PDF están en
  `.gitignore` y los hooks bloquean ambos — **si corriste `pre-commit install`**. Si no,
  no hay red de seguridad.
- **No cambiar `DVU_DTE_AMBIENTE` a producción para probar.** Cada folio quemado se paga
  con una nota de crédito.
- **No usar `float` para dinero.** En ninguna capa, en ningún lenguaje. CLP son enteros.
