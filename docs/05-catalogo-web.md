# Prototipo web: catálogo y comprobantes

Qué es, qué reemplaza y qué decisiones tomó. Es un prototipo **para mostrar**: cubre el
catálogo y la carga de comprobantes. No cablea pagos en línea ni despacho.

## Qué reemplaza

Hoy DVU opera con dos artefactos que este prototipo sustituye:

| Hoy | Con el prototipo |
|---|---|
| Catálogo PDF de 150 páginas que el vendedor lleva impreso | `/` — el mismo diseño, con buscador y precios que se corrigen en el momento |
| Grupo de WhatsApp «COMPROBANTES TRANSF.» + un bot con OCR que arma un Excel | `/vendedor` para cargar, `/cobranza` para revisar y bajar el Excel |
| Alguien corrige precios en el PDF y lo reparte de nuevo | `/admin` — se edita la celda y ya está |

El bot actual (`bot dvu cabron`) lee el export del chat, pasa las fotos por OCR y saca
una planilla con estados y colores. Acá el vendedor escribe en un formulario, así que
**no hay OCR que fallar**: el dato entra estructurado o no entra. Por eso el estado
`REVISAR OCR` no existe; los demás se conservan con el mismo nombre, la misma etiqueta y
el mismo color, porque cobranza ya los lee todos los días.

| Estado | Etiqueta en el Excel | Color |
|---|---|---|
| `listo` | LISTO PARA INGRESAR | verde |
| `falta_monto` / `falta_factura` / `falta_cliente` / `falta_dato` | FALTA … | amarillo |
| `duplicado_posible` | DUPLICADO POSIBLE | rojo |
| `abono_parcial` | ABONO PARCIAL | azul |

## Decisiones

**Las páginas son clientes de la API, no plantillas con datos.** El HTML se sirve sin
autenticar y no trae ni un dato adentro; el JavaScript pide el token a `/auth/login` y
llama a los mismos endpoints que usará la app Flutter. Así hay un solo modelo de
permisos que mantener, todo lo que la web puede hacer está documentado en `/docs`, y no
hay sesión de servidor ni superficie de CSRF.

**Sin framework, sin build, sin CDN.** Es un prototipo que tiene que abrirse en el
computador de la oficina sin instalar nada. Tres archivos: una hoja de estilos, un
módulo JS de ~130 líneas y las plantillas.

**El token va en `sessionStorage`, no en `localStorage`.** Se borra al cerrar la
pestaña. En un equipo compartido de bodega esa diferencia importa.

**El comprobante no es un pago.** Son dos tablas distintas a propósito: el `Pago` es el
hecho contable que se aplica a pedidos y se cruza con la cartola; el `Comprobante` es lo
que declaró el vendedor y todavía nadie verificó. Un comprobante puede estar mal y aun
así tiene que existir en la base, y uno solo puede terminar siendo un pago, ninguno o
parte de uno. Registrar un comprobante **no** crea un pago.

**Nada se rechaza.** Un comprobante sin monto o sin factura se guarda igual, marcado con
lo que falta. El aviso del vendedor es el dato escaso: perderlo es el único error
irreparable de este flujo. La única excepción es un monto imposible (cero o negativo),
que no es un dato incompleto sino uno erróneo y ensuciaría el total que cobranza suma.

**Un RUT mal escrito no pierde el comprobante.** Se ignora y el cliente queda sin
identificar, que es exactamente lo que pasó. Rechazar el aviso completo por un dígito
verificador sería peor.

**Sin nº de operación no se marcan duplicados.** Dos ferreterías pueden transferir el
mismo monto el mismo día. Una alarma que suena todos los días deja de mirarse.

**El monto se lee con prioridades.** Primero lo marcado como plata (`$`, separador de
miles, detrás de «por» o «abono»); recién al final un número suelto, y nunca uno que ya
se leyó como factura. «pago factura 33780» queda en FALTA MONTO en vez de inventar un
monto de 33.780: un monto equivocado entra al sistema sin que nadie lo note, un FALTA
MONTO salta a la vista.

**Nada se borra.** Desactivar un producto lo saca del catálogo y conserva la ficha,
porque puede estar referenciada en pedidos históricos. Marcar un comprobante como
ingresado lo saca de la bandeja y conserva la fila.

## Diseño

El catálogo copia la hoja impresa: banda roja con degradado, `CATALOGO / FERRETERIA`
espaciado, folio arriba a la izquierda, logo blanco a la derecha, y la grilla de siete
columnas con encabezado azul y bordes rojos. El vendedor y el cliente conocen esa hoja
de memoria; reproducirla hace que la web se lea sin explicación.

Los datos que el catálogo original no trae —marca vacía, venta mínima vacía— se marcan
con `—`, nunca se inventan. Son datos faltantes reales.

## Cómo probarlo

```bash
make up
make migrate
make seed        # usuarios de ejemplo; nunca en producción
```

Después, en <http://localhost:8000>:

1. `/` — busca «codo» o pega un código de proveedor (`PR/49573`, `KM521`).
2. `/vendedor` — entra como `vendedor@dvu.cl` / `dvu-dev-1234` y escribe un mensaje como
   se lo mandarías al grupo: *«Ferretería El Martillo, abono factura 33780 por 510.459,
   BCI op 12345678»*. El monto y la factura salen solos del texto.
3. Manda uno **sin monto** a propósito: se guarda marcado FALTA MONTO.
4. `/cobranza` — entra como `admin@dvu.cl`, mira la bandeja y baja el Excel.
5. `/admin` — corrige un precio haciendo clic en la celda.

## Qué falta

- Subir la foto del producto desde `/admin` (hoy la imagen viene del extractor del PDF).
- Categorías: el catálogo se navega buscando, no por árbol.
- Que el vendedor arme el pedido desde el catálogo — el backend ya lo soporta
  (`POST /pedidos`), falta la pantalla.
- La app Flutter offline-first, que es el destino final de este flujo.
