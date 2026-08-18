/* El carrito: la lista que el vendedor arma, vista desde el catálogo y desde /pedido.
 *
 * Son dos pantallas mostrando lo mismo, y todos los bugs que aparecieron acá fueron del
 * mismo tipo: estado que llegaba a una vista y no a la otra. Por eso estos tests miran el
 * DOM y no sólo el objeto — el objeto siempre estuvo bien.
 *
 * Ver `ayuda.mjs` para qué se prueba contra qué, y por qué nunca se envía un pedido.
 */
import test from "node:test";
import assert from "node:assert/strict";

import { abrirPagina, calma, hayStack, texto, RAZON_SALTO } from "./ayuda.mjs";

const saltar = !(await hayStack());
const opciones = { skip: saltar ? RAZON_SALTO : false };

/** Dos ferreterías de la cartera y dos productos, que es lo que piden casi todos. */
async function conDatos(pagina) {
  const clientes = [...pagina.carrito.cartera.values()];
  const { items } = await pagina.DVU.pedir("/productos?limite=3");
  assert.ok(clientes.length, "el seed tiene que dejar clientes");
  assert.ok(items.length >= 2, "el catálogo tiene que estar cargado");
  return { clientes, productos: items };
}

test("la lista creada fuera del drawer se ve seleccionada en el drawer", opciones, async () => {
  // El bug: `eligiendoCliente` era estado del drawer y sólo se apagaba en su propio
  // botón. Creándola por cualquier otro camino —«+ Nueva lista» o «Repetir» de /pedido—
  // el drawer se quedaba mostrando el selector de ferretería con la lista abierta detrás.
  const p = await abrirPagina();
  try {
    p.carrito.montarDrawer({ errores: true });
    await p.carrito.montar(p.usuario);
    const { clientes } = await conDatos(p);

    await p.carrito.crear(clientes[0]);
    p.anotar();
    p.carrito.abrirDrawer(true);

    const cambiar = p.doc.getElementById("carrito-cambiar");
    assert.ok(cambiar, "con una lista abierta el drawer ofrece «cambiar»");
    cambiar.click();
    assert.ok(p.doc.getElementById("carrito-cliente"), "y muestra el selector");

    const otra = clientes[1] || clientes[0];
    await p.carrito.crear(otra);
    p.anotar();

    assert.match(texto(p.doc, "carrito-para"), new RegExp(otra.razon_social));
    assert.equal(
      p.doc.getElementById("carrito-cliente"),
      null,
      "elegida la ferretería, el selector se va",
    );
  } finally {
    await p.limpiar();
  }
});

test("minimizar corre el panel sin perder la lista", opciones, async () => {
  // El panel se ponía encima de la foto y de la fila que el vendedor está mostrando.
  const p = await abrirPagina();
  try {
    p.carrito.montarDrawer({ errores: true });
    await p.carrito.montar(p.usuario);
    const { clientes, productos } = await conDatos(p);

    await p.carrito.crear(clientes[0]);
    p.anotar();
    p.carrito.agregar(productos[0], 2);

    p.carrito.abrirDrawer(true);
    assert.ok(
      p.doc.getElementById("carrito-boton").hidden,
      "abierto el panel, el botón flotante se esconde: no hay dos veces la misma cuenta",
    );
    assert.ok(
      p.w.document.body.classList.contains("con-carrito"),
      "y el body queda marcado, que es lo que corre la hoja para no quedar debajo",
    );

    p.doc.getElementById("carrito-minimizar").click();

    assert.ok(p.doc.getElementById("carrito").hidden, "minimizar esconde el panel");
    assert.ok(!p.doc.getElementById("carrito-boton").hidden, "y devuelve el botón");
    assert.ok(!p.w.document.body.classList.contains("con-carrito"), "la hoja se recupera");
    assert.equal(p.carrito.lista.lineas.length, 1, "la lista queda intacta");
    assert.equal(texto(p.doc, "carrito-cuenta"), "1", "con la cuenta al día");

    p.doc.getElementById("carrito-boton").click();
    assert.ok(!p.doc.getElementById("carrito").hidden, "el botón la vuelve a abrir");
  } finally {
    await p.limpiar();
  }
});

test("agregar desde el catálogo no abre el panel encima", opciones, async () => {
  const p = await abrirPagina();
  try {
    p.carrito.montarDrawer({ errores: true });
    await p.carrito.montar(p.usuario);
    const { clientes, productos } = await conDatos(p);

    await p.carrito.crear(clientes[0]);
    p.anotar();
    p.carrito.abrirDrawer(false);

    await p.carrito.agregarDesdeCatalogo(productos[0], 1);
    assert.ok(
      p.doc.getElementById("carrito").hidden,
      "minimizado se queda minimizado: el panel se abre para mirar la lista, no para sumarle",
    );
    assert.ok(p.carrito.enLista(productos[0].sku), "pero el producto entró");
    assert.ok(
      p.doc.getElementById("carrito-boton").classList.contains("late"),
      "y el botón late, que es el acuse de recibo",
    );
  } finally {
    await p.limpiar();
  }
});

test("sin lista abierta, agregar pide la ferretería y no pierde el producto", opciones, async () => {
  const p = await abrirPagina();
  try {
    p.carrito.montarDrawer({ errores: true });
    await p.carrito.montar(p.usuario);
    const { clientes, productos } = await conDatos(p);
    p.carrito.cerrar();
    p.carrito.abrirDrawer(false);

    await p.carrito.agregarDesdeCatalogo(productos[1], 1);
    assert.ok(!p.doc.getElementById("carrito").hidden, "acá sí se abre: falta elegir a quién");
    assert.ok(p.doc.getElementById("carrito-cliente"), "con el selector");

    p.doc.getElementById("carrito-cliente").value = clientes[0].rut;
    p.doc.getElementById("carrito-empezar").click();
    await calma();
    p.anotar();

    assert.ok(
      p.carrito.enLista(productos[1].sku),
      "elegida la ferretería, entra solo lo que se quería agregar: buscarlo de nuevo era el costo",
    );
  } finally {
    await p.limpiar();
  }
});

test("la tarjeta grande de /pedido sigue al carrito", opciones, async () => {
  // Mostrarla colgaba de cada sitio que creaba una lista, y el drawer no era uno: creando
  // desde el drawer, el constructor quedaba escondido con la lista abierta detrás.
  const p = await abrirPagina({ conPedido: true, ruta: "/pedido" });
  try {
    p.carrito.montarDrawer({});
    await p.carrito.montar(p.usuario);
    const { clientes } = await conDatos(p);

    // La suscripción de /pedido, reducida a lo que este test observa.
    p.carrito.alCambiar(() => {
      p.doc.getElementById("constructor").hidden = !p.carrito.lista;
      p.doc.getElementById("tarjeta-lista").hidden = !p.carrito.lista;
      if (p.carrito.lista) {
        p.doc.getElementById("titulo-lista").textContent = p.carrito.lista.cliente_razon_social;
      }
    });

    p.carrito.abrirDrawer(true);
    p.doc.getElementById("carrito-cliente").value = clientes[0].rut;
    p.doc.getElementById("carrito-empezar").click();
    await calma();
    p.anotar();

    assert.ok(!p.doc.getElementById("constructor").hidden, "el constructor aparece");
    assert.equal(texto(p.doc, "titulo-lista"), clientes[0].razon_social);
  } finally {
    await p.limpiar();
  }
});

test("los múltiplos de venta y el total los decide el servidor", opciones, async () => {
  // Regla de dominio: se vende por envase. La página traduce envases a unidades y el
  // backend rechaza lo que no calza; acá se comprueba que el drawer muestra el rechazo
  // con su arreglo a mano, que es lo que evita que el vendedor lo adivine.
  const p = await abrirPagina();
  try {
    p.carrito.montarDrawer({ errores: true });
    await p.carrito.montar(p.usuario);
    const { clientes, productos } = await conDatos(p);
    const producto = productos.find((x) => x.multiplo_venta > 1) || productos[0];

    await p.carrito.crear(clientes[0]);
    p.anotar();
    p.carrito.agregar(producto, 2);
    assert.equal(
      p.carrito.enLista(producto.sku).cantidad,
      2 * producto.multiplo_venta,
      "2 envases son 2 × el múltiplo en unidades, nunca 2 unidades",
    );

    await calma();
    assert.ok(p.carrito.totales.total_clp > 0, "el total lo calcula el servidor");
    assert.ok(p.carrito.totales.iva_clp > 0, "y el IVA también: la web no repite la regla");

    if (producto.multiplo_venta > 1) {
      p.carrito.enLista(producto.sku).cantidad += 1; // cantidad imposible, a mano
      p.carrito.cambio();
      await calma();

      assert.ok(p.carrito.totales.con_problema > 0, "la cotización marca la línea");
      const linea = p.carrito.cotizado.get(producto.sku);
      assert.ok(linea.problema, "y dice por qué");
      assert.ok(linea.cantidad_sugerida, "con la cantidad que sí calza");

      p.carrito.abrirDrawer(true);
      assert.match(
        p.doc.getElementById("carrito-cuerpo").innerHTML,
        /arreglar/,
        "el drawer ofrece el arreglo, no sólo el reproche",
      );

      p.carrito.fijar(producto.sku, linea.cantidad_sugerida);
      await calma();
      assert.equal(p.carrito.totales.con_problema, 0, "aplicado, la lista queda sana");
    }
  } finally {
    await p.limpiar();
  }
});

test("la lista se comparte entre el catálogo y /pedido", opciones, async () => {
  // Es el punto del carrito compartido: se empieza en una página y sigue en la otra.
  const p = await abrirPagina();
  try {
    p.carrito.montarDrawer({ errores: true });
    await p.carrito.montar(p.usuario);
    const { clientes, productos } = await conDatos(p);

    await p.carrito.crear(clientes[0]);
    const uuid = p.anotar().client_uuid;
    p.carrito.agregar(productos[0], 3);
    await calma();

    // Otra página: el mismo sessionStorage, el carrito arrancando de cero.
    p.carrito.lista = null;
    p.carrito.cotizado = new Map();
    await p.carrito.montar(p.usuario);

    assert.equal(p.carrito.lista?.client_uuid, uuid, "vuelve sola la lista activa");
    assert.equal(
      p.carrito.enLista(productos[0].sku).cantidad,
      3 * productos[0].multiplo_venta,
      "con sus cantidades",
    );
  } finally {
    await p.limpiar();
  }
});

test("salir no deja la lista esperando al siguiente", opciones, async () => {
  // El computador de la bodega es compartido: es el mismo motivo por el que el token va
  // en sessionStorage y no en localStorage.
  const p = await abrirPagina();
  try {
    await p.carrito.montar(p.usuario);
    const { clientes } = await conDatos(p);
    await p.carrito.crear(clientes[0]);
    p.anotar();

    assert.ok(p.w.sessionStorage.getItem("dvu_lista_activa"), "había lista activa");
    p.DVU.salir();
    assert.equal(p.w.sessionStorage.getItem("dvu_lista_activa"), null);
    assert.equal(p.w.sessionStorage.getItem("dvu_lista"), null);
  } finally {
    await p.limpiar();
  }
});

test("el UUID no necesita HTTPS", opciones, async () => {
  // `crypto.randomUUID` existe sólo en contexto seguro, y la web se sirve por HTTP plano
  // contra la IP del host: ahí no existe, y empezar una lista reventaba. El `client_uuid`
  // es la llave de idempotencia, así que tiene que ser aleatorio de verdad.
  const p = await abrirPagina();
  try {
    const v4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
    const vistos = new Set();
    for (let i = 0; i < 5000; i++) {
      const u = p.DVU.uuid();
      assert.match(u, v4);
      vistos.add(u);
    }
    assert.equal(vistos.size, 5000, "sin colisiones: una colisión es un pedido que se come a otro");
  } finally {
    await p.limpiar();
  }
});
