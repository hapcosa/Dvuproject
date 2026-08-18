/* Andamio para probar el prototipo web sobre un DOM real.
 *
 * El `dvu.js` que se carga acá es **el mismo archivo que sirve el servidor**, no una
 * copia: si se le escribe un error de sintaxis, estos tests lo dicen antes que el
 * navegador de la oficina.
 *
 * Contra qué corre: la API levantada (`make up`). Eso hace que sean tests de integración
 * de verdad —la cotización y los múltiplos los decide el dominio, no un doble— y también
 * que **escriban en la base del stack**. De ahí las dos reglas de este archivo:
 *
 *   1. Nunca se envía un pedido. Un pedido enviado no se deshace, así que quedaría para
 *      siempre en la base con la que se hacen las demostraciones —y con un folio gastado
 *      en la numeración—. Enviar ya está cubierto por los tests de pytest, que corren
 *      contra `<base>_test`. Pasó al revés: probando a mano quedaron cuatro pedidos.
 *   2. Toda lista que se crea, se anula. `limpiar()` corre pase lo que pase.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { webcrypto } from "node:crypto";

import { JSDOM } from "jsdom";

const AQUI = dirname(fileURLToPath(import.meta.url));
const WEB = resolve(AQUI, "../../src/dvu/web");

/** Dónde está la API. En el contenedor es `api:8000`; desde el host, localhost. */
export const BASE = process.env.DVU_WEB_URL || "http://localhost:8000";
export const API = `${BASE}/api/v1`;

/** Las credenciales del `make seed`. Sin ellas no hay nada que probar: ver `hayStack`. */
const VENDEDOR = { email: "vendedor@dvu.cl", password: "dvu-dev-1234" };

/** ¿Se puede correr? Sin stack levantado o sin seed, estos tests se saltan en vez de
 *  fallar: nadie debería quedarse en rojo por no tener el stack arriba, igual que en
 *  `conftest.py`. */
export async function hayStack() {
  try {
    const r = await fetch(`${API}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(VENDEDOR),
      signal: AbortSignal.timeout(4000),
    });
    return r.ok;
  } catch {
    return false;
  }
}

export const RAZON_SALTO =
  `sin API en ${BASE} o sin datos de ejemplo: corre \`make up\` y \`make seed\``;

/** Lee un parcial de Jinja como HTML, sacándole los comentarios `{# … #}`.
 *
 *  No hay motor de plantillas acá a propósito: lo que se prueba es el comportamiento del
 *  JS sobre el marcado, y los parciales del carrito no tienen lógica de Jinja. Si alguna
 *  vez la tuvieran, esto lo delataría al fallar de una forma rara — mejor eso que probar
 *  contra una copia del marcado que se desactualiza en silencio. */
function parcial(nombre) {
  return readFileSync(`${WEB}/templates/${nombre}`, "utf8").replace(/\{#[\s\S]*?#\}/g, "");
}

/** El esqueleto de /pedido que el carrito toca. Sólo los huecos, no la página entera:
 *  lo que se prueba es qué hace el carrito con ellos. */
const ESQUELETO_PEDIDO = `
  <div id="app">
    <div id="mensaje"></div>
    <div id="listas"></div><div id="pista-listas"></div>
    <section id="constructor" hidden>
      <h2 id="titulo-lista"></h2><p id="sub-lista"></p><span id="guardado"></span>
    </section>
    <section id="tarjeta-lista" hidden>
      <span id="cuenta-lineas"></span><div id="aviso-lista" hidden></div>
      <table><tbody id="lineas"></tbody><tfoot id="totales"></tfoot></table>
      <input id="observaciones">
    </section>
    <table><tbody id="resultados"></tbody></table><div id="mas"></div>
  </div>`;

/** Levanta una página con el carrito montado y sesión de vendedor iniciada. */
export async function abrirPagina({ conPedido = false, ruta = "/" } = {}) {
  const dom = new JSDOM(
    `<!doctype html><html><head><meta name="dvu-api" content="${API}">
     <style>${readFileSync(`${WEB}/static/dvu.css`, "utf8")}</style>
     <body>
       <nav class="nav"><span id="sesion"></span></nav>
       <main class="hoja">${conPedido ? ESQUELETO_PEDIDO : ""}</main>
       ${parcial("_carrito.html")}
     </body></html>`,
    { url: BASE + ruta, runScripts: "dangerously", pretendToBeVisual: true },
  );

  const w = dom.window;
  w.fetch = (u, o) => fetch(u, o); // jsdom no trae fetch; se usa el de node
  // El punto de `DVU.uuid`: contexto inseguro, sin `randomUUID`. Es como corre en la
  // oficina, servido por HTTP plano contra la IP del host.
  Object.defineProperty(w, "crypto", {
    value: { getRandomValues: (a) => webcrypto.getRandomValues(a) },
    configurable: true,
  });

  const DVU = w.eval(`${readFileSync(`${WEB}/static/dvu.js`, "utf8")}; DVU;`);

  const sesion = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(VENDEDOR),
  }).then((r) => r.json());
  w.sessionStorage.setItem("dvu_token", sesion.access_token);
  if (sesion.refresh_token) w.sessionStorage.setItem("dvu_refresh", sesion.refresh_token);

  const usuario = await DVU.yo();
  const creados = new Set();

  return {
    w,
    DVU,
    usuario,
    doc: w.document,
    carrito: DVU.carrito,

    /** Anota el borrador abierto para borrarlo al final. Se llama después de crear. */
    anotar() {
      if (DVU.carrito.lista) creados.add(DVU.carrito.lista.client_uuid);
      return DVU.carrito.lista;
    },

    /** Anula las listas que este test creó y cierra el DOM. Va en un `finally`: dejarlas
     *  vivas ensucia la bandeja de quien esté demostrando el sistema.
     *
     *  `DELETE` no borra la fila, la deja en `anulado` —el dominio guarda la historia—.
     *  Así que cada corrida deja una fila muerta: no se ve en ninguna pantalla, pero está.
     *  Es el precio de correr contra la base real, y es mucho más barato que un pedido. */
    async limpiar() {
      for (const uuid of creados) {
        await fetch(`${API}/pedidos/borradores/${uuid}`, {
          method: "DELETE",
          headers: { Authorization: `Bearer ${sesion.access_token}` },
        }).catch(() => {});
      }
      w.close();
    },
  };
}

/** Los debounce de guardado (700 ms) y cotización (300 ms) son parte del diseño: se
 *  espera lo que se espera en la página, no se los desactiva. */
export const calma = (ms = 1400) => new Promise((r) => setTimeout(r, ms));

export const texto = (doc, id) => (doc.getElementById(id)?.textContent || "").trim();

/** ¿Se ve? Mira el estilo calculado, con la hoja real aplicada.
 *
 *  **Lo que esto NO prueba:** que `hidden` gane sobre el `display:` del CSS. jsdom aplica
 *  el `hidden` del navegador con más fuerza de la que le corresponde y responde `none`
 *  donde un navegador de verdad responde `flex`. Con `.carrito { display: flex }` el panel
 *  quedaba visible para siempre en pantalla y jsdom decía que estaba escondido, en la 26 y
 *  en la 30. Esa conflicto lo cuida `test_el_atributo_hidden_le_gana_al_display` en
 *  pytest, mirando la hoja misma, que es donde la respuesta es determinista.
 *
 *  Sirve igual: cubre que ninguna **otra** regla lo esconda, y es más que mirar el
 *  atributo a secas. */
export function seVe(w, id) {
  const el = w.document.getElementById(id);
  if (!el) return false;
  return w.getComputedStyle(el).display !== "none";
}
