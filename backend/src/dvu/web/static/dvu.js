/* Cliente mínimo de la API DVU.
 *
 * Sin framework y sin build: el prototipo tiene que abrirse en el navegador de
 * cualquiera sin instalar nada. Todo lo que hace pasa por los mismos endpoints
 * documentados en /docs — la web no tiene atajos propios contra la base.
 *
 * El token va en sessionStorage y no en localStorage: se borra al cerrar la pestaña.
 * En un equipo compartido de bodega esa diferencia importa.
 *
 * Se guarda también el refresh token y el 401 se reintenta una vez renovando. Sin eso el
 * access token dura una hora y la pestaña que quedó abierta desde la mañana falla en la
 * primera acción de la tarde con «la sesión venció», que es justo cuando el vendedor
 * está frente al cliente.
 */

const DVU = (() => {
  const base = document.querySelector('meta[name="dvu-api"]').content;
  const CLAVE = "dvu_token";
  const CLAVE_REFRESH = "dvu_refresh";

  //: Los dos endpoints que **entregan** sesión. Un 401 acá significa «esas credenciales
  //: no sirven», no «la tuya venció»: son estados opuestos y confundirlos le decía «La
  //: sesión venció, ingresa de nuevo» a quien nunca había entrado y sólo tecleó mal la
  //: contraseña —el consejo, además, era exactamente lo que estaba intentando hacer—.
  const ENTREGAN_SESION = ["/auth/login", "/auth/refresh"];

  //: Qué rol abre qué página. Vive acá y no en cada plantilla porque se usa en dos
  //: lados —el aviso de «esta página no es para tu cuenta» y los atajos que ofrece— y
  //: dos copias se desincronizan. `admin` entra a todas, igual que en `exige_rol`.
  const PAGINAS = [
    { ruta: "/", nombre: "Catálogo", roles: null },
    { ruta: "/pedido", nombre: "Armar pedido", roles: ["vendedor", "cliente"] },
    { ruta: "/vendedor", nombre: "Comprobantes", roles: ["vendedor"] },
    { ruta: "/cobranza", nombre: "Cobranza", roles: ["admin"] },
    { ruta: "/admin", nombre: "Administrar catálogo", roles: ["admin"] },
  ];

  const alcanza = (rol, roles) => !roles || rol === "admin" || roles.includes(rol);

  const token = () => sessionStorage.getItem(CLAVE);

  //: Una sola renovación en vuelo. Si tres requests vencen a la vez —pasa al volver de
  //: dejar el computador— tres refresh en paralelo se pisan y dos quedan inválidos.
  let renovacion = null;

  function renovar() {
    const refresh = sessionStorage.getItem(CLAVE_REFRESH);
    if (!refresh) return Promise.resolve(false);
    renovacion ||= fetch(base + "/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((datos) => {
        if (!datos) return false;
        guardarSesion(datos);
        return true;
      })
      .catch(() => false)
      .finally(() => { renovacion = null; });
    return renovacion;
  }

  function guardarSesion(datos) {
    sessionStorage.setItem(CLAVE, datos.access_token);
    if (datos.refresh_token) sessionStorage.setItem(CLAVE_REFRESH, datos.refresh_token);
  }

  //: Quién entró, para poder decir «tu cuenta es vendedor» cuando el servidor responde
  //: 403. El servidor sabe qué rol hace falta y el navegador sabe cuál tiene: el mensaje
  //: entendible necesita las dos mitades.
  let quienEntro = null;

  function cerrarSesion() {
    sessionStorage.removeItem(CLAVE);
    sessionStorage.removeItem(CLAVE_REFRESH);
    // La lista se va con la sesión. En el computador compartido de la bodega, salir y
    // que el siguiente encuentre abierta la lista a medias de otra ferretería es
    // exactamente lo que el token en sessionStorage estaba evitando.
    sessionStorage.removeItem(CLAVE_ACTIVA);
    sessionStorage.removeItem(CLAVE_LOCAL);
    quienEntro = null;
  }

  /** Un error de una línea, en palabras. Llegan de dos formas: la validación de FastAPI
   *  (`loc`/`msg`) y las reglas del dominio, que hablan de un SKU (`sku`/`error`). */
  const explicar = (e) =>
    e?.error ? `${e.sku}: ${e.error}` : `${e?.loc?.slice(1).join(".")}: ${e?.msg}`;

  async function pedir(ruta, opciones = {}, reintentando = false) {
    const cabeceras = { ...(opciones.headers || {}) };
    // El cuerpo se arma aparte y no se pisa `opciones`: si hay que reintentar tras
    // renovar, un `JSON.stringify` sobre lo ya serializado mandaría una cadena escapada.
    let cuerpo = opciones.body;
    if (cuerpo && !(cuerpo instanceof FormData)) {
      cabeceras["Content-Type"] = "application/json";
      cuerpo = JSON.stringify(cuerpo);
    }
    if (token()) cabeceras.Authorization = `Bearer ${token()}`;

    const respuesta = await fetch(base + ruta, { ...opciones, body: cuerpo, headers: cabeceras });

    // Un 401 sólo es «se venció» si mandamos una credencial y el servidor la rechazó.
    // Si no mandamos ninguna, o si el que contesta es el endpoint que las entrega, el
    // 401 es su respuesta y su `detail` dice lo que de verdad pasó: se deja caer al
    // manejo de abajo, que lo muestra tal cual.
    const entregaSesion = ENTREGAN_SESION.some((r) => ruta.startsWith(r));
    if (respuesta.status === 401 && cabeceras.Authorization && !entregaSesion) {
      if (!reintentando && (await renovar())) return pedir(ruta, opciones, true);
      cerrarSesion();
      throw Object.assign(new Error("La sesión venció. Ingresa de nuevo."), {
        estado: 401,
        vencida: true,
      });
    }
    if (!respuesta.ok) {
      // El detalle del backend es el mensaje útil (qué falta, qué chocó). Se muestra
      // tal cual en vez de un "error 422" que no le dice nada a nadie.
      let detalle = `Error ${respuesta.status}`;
      let crudo = null;
      try {
        const cuerpo = await respuesta.json();
        crudo = cuerpo.detail;
        if (typeof crudo === "string") detalle = crudo;
        else if (Array.isArray(crudo)) detalle = crudo.map(explicar).join(" · ");
      } catch { /* respuesta sin JSON: queda el código */ }

      // Un 403 no es un error de datos: es «tu cuenta no puede hacer esto». Dicho así
      // —con el rol que tienes, no sólo el que falta— se entiende sin adivinar.
      if (respuesta.status === 403) {
        const tuyo = quienEntro ? ` Tu cuenta es «${quienEntro.rol}».` : "";
        detalle = `No puedes hacer esto: ${detalle.replace(/^Se requiere/, "se requiere")}.${tuyo}`;
      }

      // El detalle crudo viaja en el error para que quien llama pueda hacer algo más
      // que mostrarlo: marcar las líneas malas del pedido, por ejemplo.
      const error = new Error(detalle);
      error.estado = respuesta.status;
      error.detalle = crudo;
      error.sinPermiso = respuesta.status === 403;
      throw error;
    }
    if (respuesta.status === 204) return null;
    const tipo = respuesta.headers.get("content-type") || "";
    return tipo.includes("json") ? respuesta.json() : respuesta.blob();
  }

  async function ingresar(email, password) {
    const datos = await pedir("/auth/login", { method: "POST", body: { email, password } });
    guardarSesion(datos);
    return yo();
  }

  async function yo() {
    quienEntro = await pedir("/auth/yo");
    return quienEntro;
  }

  const salir = () => {
    cerrarSesion();
    location.href = "/";
  };

  /** Descarga chica: el archivo entra en memoria sin problema (un .xlsx son cientos de KB). */
  async function descargar(ruta, nombre) {
    const blob = await pedir(ruta);
    const url = URL.createObjectURL(blob);
    const enlace = Object.assign(document.createElement("a"), { href: url, download: nombre });
    document.body.appendChild(enlace);
    enlace.click();
    enlace.remove();
    URL.revokeObjectURL(url);
  }

  /** Descarga grande: la baja el navegador, no nosotros.
   *
   *  El catálogo con fotos pesa decenas de MB. Por `fetch` hay que esperar a tenerlo
   *  entero en memoria antes de poder escribir un solo byte a disco, sin barra de
   *  progreso y sin forma de cancelar: en un celular eso se queda pegado o se cae. Acá se
   *  pide un token corto (`POST /auth/descarga`) y se navega a la URL, que es la vía por
   *  la que el navegador sabe bajar archivos grandes desde siempre.
   *
   *  El token va en la query porque al navegar no hay dónde poner el `Authorization`.
   *  Dura dos minutos y sólo sirve para leer; ver `dvu.api.deps.usuario_descargando`. */
  async function descargarGrande(ruta) {
    const permiso = await pedir("/auth/descarga", { method: "POST" });
    const url = `${base}${ruta}${ruta.includes("?") ? "&" : "?"}token=` +
      encodeURIComponent(permiso.token);
    // `download` en vez de `location.href`: si el servidor respondiera un error en vez
    // del archivo, navegar dejaría al usuario mirando un JSON en lugar del catálogo.
    const enlace = Object.assign(document.createElement("a"), { href: url, download: "" });
    document.body.appendChild(enlace);
    enlace.click();
    enlace.remove();
  }

  /* --- utilidades de presentación --------------------------------------- */

  // CLP sin decimales, siempre. El peso no tiene centavos.
  const pesos = (n) =>
    n === null || n === undefined ? "" : "$ " + Number(n).toLocaleString("es-CL");

  const escapar = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
    );

  /** Un UUID v4, haya o no `crypto.randomUUID`.
   *
   *  `randomUUID` existe **sólo en contexto seguro**: HTTPS o localhost. La web se sirve
   *  por HTTP plano contra la IP de la máquina, así que en el computador de la oficina
   *  —el único lugar donde esto corre de verdad— no existe, y crear una lista reventaba
   *  con «crypto.randomUUID is not a function». En el navegador del que programa, en
   *  localhost, funcionaba siempre.
   *
   *  `getRandomValues` sí está en todo contexto, y es lo que importa: el `client_uuid` es
   *  la llave de idempotencia con la que el backend reconoce un reenvío. Sacarlo de
   *  `Math.random` daría colisiones, y una colisión acá es un pedido que se come a otro. */
  function uuid() {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40; // versión 4
    b[8] = (b[8] & 0x3f) | 0x80; // variante RFC 4122
    const hex = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-` +
      `${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  /** Dato que el catálogo original no trae: se marca, no se inventa. */
  const oVacio = (valor) =>
    valor ? escapar(valor) : '<span class="vacio">—</span>';

  function avisar(elemento, mensaje, tipo = "info") {
    elemento.className = `aviso ${tipo}`;
    elemento.textContent = mensaje;
    elemento.hidden = false;
  }

  /** Deja pasar `ms` de calma antes de ejecutar; cada llamada nueva reinicia la espera.
   *
   *  Es lo que hace que buscar mientras se escribe no sea una petición por tecla, y que
   *  apretar «+» cinco veces seguidas guarde una vez y no cinco. Devuelve una función
   *  con `.ahora(...)` para cuando no se puede esperar —al enviar, por ejemplo—. */
  function aplazar(fn, ms) {
    let reloj = null;
    const aplazada = (...args) => {
      clearTimeout(reloj);
      reloj = setTimeout(() => fn(...args), ms);
    };
    aplazada.ahora = (...args) => {
      clearTimeout(reloj);
      return fn(...args);
    };
    aplazada.cancelar = () => clearTimeout(reloj);
    return aplazada;
  }

  /** Abre WhatsApp con el texto listo para elegir a quién mandárselo.
   *
   *  El pedido hoy viaja por WhatsApp y va a seguir viajando un tiempo: el vendedor le
   *  manda el resumen al ferretero para que confirme. Sin esto, copiaría a mano. */
  const porWhatsapp = (texto) =>
    window.open(`https://wa.me/?text=${encodeURIComponent(texto)}`, "_blank", "noopener");

  /* --- sesión ------------------------------------------------------------- */

  /** La URL de la página de ingreso, con dónde volver.
   *
   *  `volver` se usa después como destino de una navegación, así que sólo se acepta una
   *  ruta de este sitio: una URL absoluta ahí es un redirect abierto, y el correo de
   *  «entra al sistema» es exactamente el lugar donde eso se aprovecha. */
  function urlDeIngreso(destino = location.pathname + location.search) {
    return `/ingresar?volver=${encodeURIComponent(destino)}`;
  }

  const rutaLocal = (v) => (v && v.startsWith("/") && !v.startsWith("//") ? v : "/");

  /** Deja la sesión visible en la barra de arriba, en toda página.
   *
   *  Se pinta siempre, también sin entrar: saber que se puede entrar —y por dónde— es
   *  parte de la página. Antes el hueco quedaba vacío y la única puerta era el formulario
   *  enterrado dentro de la página privada a la que ya no podías llegar. */
  function pintarSesion(usuario) {
    const sesion = document.getElementById("sesion");
    if (!sesion) return;

    if (!usuario) {
      const aca = location.pathname === "/ingresar";
      sesion.innerHTML = aca
        ? '<span class="anonimo">Sin sesión</span>'
        : `<a class="entrar" href="${escapar(urlDeIngreso())}">Ingresar</a>`;
      return;
    }
    sesion.innerHTML =
      `<span class="quien">${escapar(usuario.nombre)}</span>` +
      `<span class="pastilla rol">${escapar(usuario.rol)}</span>` +
      '<a href="#" id="salir">Salir</a>';
    document.getElementById("salir").onclick = (e) => { e.preventDefault(); salir(); };
  }

  /** Explica por qué la página está en blanco y ofrece a dónde sí se puede ir.
   *
   *  Mandar a la pantalla de ingreso sería mentir: la sesión está perfecta, lo que no
   *  alcanza es el rol, y volver a entrar con la misma cuenta da lo mismo. */
  function negarAcceso(usuario, roles) {
    const bloqueo = document.getElementById("sin-acceso");
    const app = document.getElementById("app");
    if (app) app.hidden = true;
    if (!bloqueo) return;

    const suyas = PAGINAS.filter((p) => alcanza(usuario.rol, p.roles) && p.ruta !== location.pathname);
    bloqueo.innerHTML =
      "<h2>Esta página no es para tu cuenta</h2>" +
      `<div class="aviso error">Esta página es para ${escapar(roles.join(" o "))}. ` +
      `Entraste como ${escapar(usuario.nombre)}, que es ${escapar(usuario.rol)}.</div>` +
      (suyas.length
        ? '<p class="pista">Con tu cuenta puedes usar: ' +
          suyas.map((p) => `<a href="${p.ruta}">${escapar(p.nombre)}</a>`).join(" · ") +
          ".</p>"
        : "") +
      `<p class="pista">Si te equivocaste de cuenta, <a href="${escapar(urlDeIngreso())}">` +
      "entra con otra</a>.</p>";
    bloqueo.hidden = false;
  }

  /** Deja ver la página sólo a quien corresponde, y explica el resto de los casos.
   *
   *  Tres desenlaces distintos que antes eran uno solo: sin sesión se va a ingresar, con
   *  sesión y sin rol se explica, y si el servidor no contesta se dice eso —y no «la
   *  sesión venció», que mandaba a reingresar contra un servidor caído—. */
  let protegida = false;

  async function proteger({ rol, alIngresar }) {
    const app = document.getElementById("app");
    const bloqueo = document.getElementById("sin-acceso");
    protegida = true;

    if (!token()) {
      location.href = urlDeIngreso();
      return;
    }

    let usuario;
    try {
      usuario = await yo();
    } catch (e) {
      if (e.estado === 401) {
        location.href = urlDeIngreso();
        return;
      }
      pintarSesion(null);
      if (bloqueo) {
        bloqueo.innerHTML =
          "<h2>No se pudo comprobar tu sesión</h2>" +
          `<div class="aviso error">${escapar(e.message)}</div>` +
          '<p class="pista">El servidor no está respondiendo. Vuelve a cargar la página ' +
          "en un momento.</p>";
        bloqueo.hidden = false;
      }
      return;
    }

    pintarSesion(usuario);
    if (rol && !alcanza(usuario.rol, rol)) return negarAcceso(usuario, rol);

    if (bloqueo) bloqueo.hidden = true;
    if (app) app.hidden = false;
    alIngresar?.(usuario);
  }

  /** La barra de arriba se pinta sola en toda página, también en las públicas.
   *
   *  En las privadas ya lo hizo `proteger`, que corre antes —el `<script>` del final del
   *  body se ejecuta antes del `DOMContentLoaded`—: preguntar de nuevo quién soy sería
   *  un `/auth/yo` por página sin motivo. */
  async function arrancar() {
    if (protegida) return;
    const usuario = await quienSoy();
    if (!usuario) cerrarSesion();
    pintarSesion(usuario);
  }
  document.addEventListener("DOMContentLoaded", arrancar);

  /** Quién entró, preguntado una sola vez por página.
   *
   *  La barra de arriba y el carrito lo necesitan los dos, y los dos corren al cargar:
   *  sin compartir la respuesta en vuelo, cada página abierta pedía `/auth/yo` dos veces
   *  para pintar lo mismo. Devuelve `null` en vez de tirar —no haber entrado no es un
   *  error que mostrar— y por eso el que necesite distinguir un token vencido de un
   *  servidor caído usa `yo()` directo, como hace `proteger`. */
  let sesionEnVuelo = null;
  function quienSoy() {
    if (quienEntro) return Promise.resolve(quienEntro);
    if (!token()) return Promise.resolve(null);
    sesionEnVuelo ||= yo()
      .catch(() => null)
      .finally(() => { sesionEnVuelo = null; });
    return sesionEnVuelo;
  }

  /* --- el carrito ----------------------------------------------------------
   *
   * La lista que se está armando, compartida por el catálogo y por /pedido. Vive acá y no
   * dentro de cada página porque son dos pantallas que muestran lo mismo: dos copias del
   * estado se desincronizan, y la que se desincroniza es la que el vendedor está mirando
   * cuando el ferretero le pregunta cuánto va.
   *
   * Lo que el catálogo agrega cae en la **lista activa**, que se recuerda entre páginas.
   * Sin ese concepto, «Agregar» desde el catálogo no tiene a qué ferretería atribuirse:
   * un pedido siempre es de alguien.
   */

  const CLAVE_ACTIVA = "dvu_lista_activa";
  const CLAVE_LOCAL = "dvu_lista";

  /** El vendedor guarda sus listas en el servidor: la mañana son cinco ferreterías y una
   *  lista a medias que se pierde por cerrar la pestaña es trabajo que hay que rehacer
   *  con el cliente al lado. */
  const enServidor = {
    guardaVarias: true,
    listar: () => pedir("/pedidos/borradores"),
    crear: (cliente, lineas = []) =>
      pedir("/pedidos/borradores", {
        method: "POST",
        body: {
          client_uuid: uuid(),
          cliente_rut: cliente.rut,
          lineas: lineas.map((l) => ({ sku: l.sku, cantidad: l.cantidad })),
        },
      }),
    guardar: (l) =>
      pedir(`/pedidos/borradores/${l.client_uuid}`, {
        method: "PUT",
        body: {
          cliente_rut: l.cliente_rut,
          observaciones: l.observaciones || null,
          lineas: l.lineas.map((x) => ({ sku: x.sku, cantidad: x.cantidad })),
        },
      }),
    descartar: (l) => pedir(`/pedidos/borradores/${l.client_uuid}`, { method: "DELETE" }),
    enviar: (l) => pedir(`/pedidos/borradores/${l.client_uuid}/enviar`, { method: "POST" }),
  };

  /** Un usuario con rol `cliente` no tiene a quién atribuirle varias listas —no hay
   *  vínculo usuario↔cliente— así que arma una sola en el navegador. */
  const enNavegador = {
    guardaVarias: false,
    listar: () => {
      const guardada = JSON.parse(sessionStorage.getItem(CLAVE_LOCAL) || "null");
      return Promise.resolve(guardada ? [guardada] : []);
    },
    crear: (cliente, lineas = []) =>
      Promise.resolve({
        client_uuid: uuid(),
        cliente_rut: cliente.rut,
        cliente_razon_social: cliente.razon_social,
        observaciones: null,
        lineas,
      }),
    guardar: (l) => {
      sessionStorage.setItem(CLAVE_LOCAL, JSON.stringify(l));
      return Promise.resolve(l);
    },
    descartar: () => {
      sessionStorage.removeItem(CLAVE_LOCAL);
      return Promise.resolve();
    },
    enviar: (l) =>
      pedir("/pedidos", {
        method: "POST",
        body: {
          client_uuid: l.client_uuid,
          cliente_rut: l.cliente_rut,
          observaciones: l.observaciones || null,
          creado_en_dispositivo: new Date().toISOString(),
          lineas: l.lineas.map((x) => ({ sku: x.sku, cantidad: x.cantidad })),
        },
      }),
  };

  /** Cuántos envases son esas unidades. La página habla de envases; el backend, de
   *  unidades. Traducir en un solo lugar evita el `/multiplo` regado por todos lados. */
  const envasesDe = (linea) => Math.floor(linea.cantidad / (linea.multiplo_venta || 1));

  /** Normaliza lo que devuelve la API al mínimo que las páginas necesitan. */
  function aLista(borrador) {
    return {
      client_uuid: borrador.client_uuid,
      cliente_rut: borrador.cliente_rut,
      cliente_razon_social: borrador.cliente_razon_social || borrador.cliente_rut,
      observaciones: borrador.observaciones || "",
      lineas: (borrador.lineas || []).map((l) => ({
        sku: l.sku,
        descripcion: l.descripcion,
        cantidad: l.cantidad,
        multiplo_venta: l.multiplo_venta,
      })),
    };
  }

  const SIN_TOTALES = { neto_clp: 0, iva_clp: 0, total_clp: 0, con_problema: 0 };

  const carrito = {
    lista: null,
    almacen: enNavegador,
    /** Lo último que respondió `/pedidos/cotizar`, por SKU. */
    cotizado: new Map(),
    totales: { ...SIN_TOTALES },
    /** Los clientes de la cartera, por RUT. */
    cartera: new Map(),
    /** Las listas abiertas, para el selector del drawer. */
    borradores: [],
    estadoGuardado: "",
    montado: false,
  };

  const oyentes = new Set();
  /** Se suscriben el drawer y la tarjeta grande de /pedido. Una sola fuente, dos vistas. */
  carrito.alCambiar = (fn) => { oyentes.add(fn); return () => oyentes.delete(fn); };
  const avisarCambio = () => oyentes.forEach((fn) => fn(carrito));

  carrito.envases = envasesDe;
  carrito.hayLineas = () => Boolean(carrito.lista?.lineas.length);

  function marcarGuardado(texto, clase = "") {
    carrito.estadoGuardado = texto;
    carrito.claseGuardado = clase;
    avisarCambio();
  }

  const guardarPronto = aplazar(async () => {
    if (!carrito.lista) return;
    marcarGuardado("Guardando…");
    try {
      await carrito.almacen.guardar(carrito.lista);
      marcarGuardado("Guardado", "ok");
      if (carrito.almacen.guardaVarias) carrito.refrescarBorradores();
    } catch (e) {
      // La lista completa viaja en cada guardado, así que el próximo cambio reintenta
      // solo y sin arrastrar lo que se perdió. Mientras tanto se dice, no se oculta.
      marcarGuardado("Sin guardar", "error");
      carrito.error = `No se pudo guardar la lista: ${e.message}`;
      avisarCambio();
    }
  }, 700);

  const cotizarPronto = aplazar(async () => {
    if (!carrito.lista) return;
    if (!carrito.lista.lineas.length) {
      carrito.cotizado = new Map();
      carrito.totales = { ...SIN_TOTALES };
      return avisarCambio();
    }
    try {
      const r = await pedir("/pedidos/cotizar", {
        method: "POST",
        body: { lineas: carrito.lista.lineas.map((l) => ({ sku: l.sku, cantidad: l.cantidad })) },
      });
      carrito.cotizado = new Map(r.lineas.map((l) => [l.sku, l]));
      carrito.totales = r;
      avisarCambio();
    } catch (e) {
      carrito.error = e.message;
      avisarCambio();
    }
  }, 300);

  carrito.guardarPronto = guardarPronto;

  /** Un cambio: se pinta al tiro y se guarda y cotiza cuando pare la mano. */
  function cambio() {
    avisarCambio();
    guardarPronto();
    cotizarPronto();
  }
  carrito.cambio = cambio;

  carrito.abrir = function (borrador) {
    carrito.lista = aLista(borrador);
    carrito.cotizado = new Map();
    carrito.totales = { ...SIN_TOTALES };
    // Elegir ferretería terminó: ya hay una. Se apaga acá y no en el botón del drawer
    // porque la lista también nace desde fuera —«+ Nueva lista» y «Repetir» en /pedido—,
    // y en esos casos el drawer se quedaba mostrando el selector como si no hubiera nada
    // elegido, con la lista recién creada abierta detrás.
    eligiendoCliente = false;
    if (carrito.almacen.guardaVarias) sessionStorage.setItem(CLAVE_ACTIVA, carrito.lista.client_uuid);
    marcarGuardado("Guardado", "ok");
    cotizarPronto();
    avisarCambio();
    return carrito.lista;
  };

  carrito.cerrar = function () {
    carrito.lista = null;
    carrito.cotizado = new Map();
    carrito.totales = { ...SIN_TOTALES };
    sessionStorage.removeItem(CLAVE_ACTIVA);
    avisarCambio();
  };

  /** Agrega envases, no unidades: el catálogo y el vendedor hablan de cajas.
   *
   *  La cantidad que viaja al backend son unidades y siempre múltiplo del envase, que es
   *  la regla que no se puede romper: un pedido de 5 de algo que va de a 12 no se
   *  despacha. */
  carrito.agregar = function (producto, cuantos = 1) {
    if (!carrito.lista) return null;
    const multiplo = producto.multiplo_venta || 1;
    const existente = carrito.lista.lineas.find((l) => l.sku === producto.sku);
    if (existente) existente.cantidad += cuantos * multiplo;
    else
      carrito.lista.lineas.push({
        sku: producto.sku,
        descripcion: producto.descripcion,
        cantidad: cuantos * multiplo,
        multiplo_venta: multiplo,
      });
    cambio();
    return existente || carrito.lista.lineas.at(-1);
  };

  carrito.mover = function (sku, paso) {
    const linea = carrito.lista?.lineas.find((l) => l.sku === sku);
    if (!linea) return;
    const cuantos = envasesDe(linea) + paso;
    if (cuantos <= 0) return carrito.quitar(sku);
    linea.cantidad = cuantos * (linea.multiplo_venta || 1);
    cambio();
  };

  carrito.fijar = function (sku, cantidad) {
    const linea = carrito.lista?.lineas.find((l) => l.sku === sku);
    if (!linea) return;
    linea.cantidad = Number(cantidad);
    cambio();
  };

  carrito.quitar = function (sku) {
    if (!carrito.lista) return;
    carrito.lista.lineas = carrito.lista.lineas.filter((l) => l.sku !== sku);
    cambio();
  };

  carrito.enLista = (sku) => carrito.lista?.lineas.find((l) => l.sku === sku);

  carrito.crear = async function (cliente, lineas = []) {
    const borrador = await carrito.almacen.crear(cliente, lineas);
    if (!carrito.almacen.guardaVarias) await carrito.almacen.guardar(aLista(borrador));
    await carrito.refrescarBorradores();
    return carrito.abrir(borrador);
  };

  carrito.refrescarBorradores = async function () {
    try {
      carrito.borradores = await carrito.almacen.listar();
    } catch { carrito.borradores = []; }
    avisarCambio();
    return carrito.borradores;
  };

  /** Pinta en la lista lo que el backend rechazó, línea por línea. */
  carrito.marcarProblemas = function (detalle) {
    let cuantas = 0;
    for (const problema of detalle) {
      const linea = carrito.cotizado.get(problema.sku);
      if (!linea) continue;
      linea.problema = problema.error;
      linea.cantidad_sugerida = problema.cantidad_sugerida ?? null;
      cuantas += 1;
    }
    carrito.totales = { ...carrito.totales, con_problema: cuantas };
    avisarCambio();
  };

  /** Manda la lista y devuelve el pedido. Lo pendiente se guarda antes: el borrador del
   *  servidor es lo que se convierte en pedido, no lo que hay en pantalla. */
  carrito.enviar = async function () {
    guardarPronto.cancelar();
    await carrito.almacen.guardar(carrito.lista);
    const pedido = await carrito.almacen.enviar(carrito.lista);
    if (!carrito.almacen.guardaVarias) sessionStorage.removeItem(CLAVE_LOCAL);
    carrito.cerrar();
    carrito.refrescarBorradores();
    return pedido;
  };

  carrito.descartar = async function () {
    await carrito.almacen.descartar(carrito.lista);
    carrito.cerrar();
    carrito.refrescarBorradores();
  };

  /** El resumen que el vendedor le manda al ferretero para que confirme. */
  carrito.resumenWhatsapp = (pedido) => {
    const lineas = pedido.lineas
      .map((l) =>
        `• ${l.cantidad / l.multiplo_venta} × ${l.descripcion} ` +
        `(${l.cantidad} un.) ${pesos(l.total_linea_clp)}`)
      .join("\n");
    return `Pedido ${pedido.numero}\n${pedido.cliente_razon_social}\n\n${lineas}\n\n` +
      `Neto ${pesos(pedido.neto_clp)}\nIVA ${pesos(pedido.iva_clp)}\nTotal ${pesos(pedido.total_clp)}`;
  };

  carrito.cargarCartera = async function () {
    try {
      const r = await pedir("/clientes?limite=200");
      r.items.forEach((c) => carrito.cartera.set(c.rut, c));
    } catch { /* sin cartera el selector lo dice; no es motivo para tumbar la página */ }
    return carrito.cartera;
  };

  /** Resuelve lo escrito contra la cartera: el desplegable de doscientas ferreterías no
   *  se puede usar en un celular, así que se escribe y se filtra. */
  carrito.resolverCliente = function (texto) {
    const escrito = String(texto || "").trim().toLowerCase();
    if (!escrito) return null;
    for (const cliente of carrito.cartera.values()) {
      if (`${cliente.razon_social} — ${cliente.rut}`.toLowerCase() === escrito) return cliente;
      if (cliente.rut.toLowerCase() === escrito) return cliente;
    }
    const parciales = [...carrito.cartera.values()].filter((c) =>
      `${c.razon_social} ${c.rut}`.toLowerCase().includes(escrito));
    return parciales.length === 1 ? parciales[0] : null;
  };

  /* --- el drawer -----------------------------------------------------------
   *
   * No es un `<dialog>`: en el catálogo se sigue buscando con el drawer abierto, así que
   * no puede ser modal ni robar el foco. En el celular tapa la pantalla, que es lo que
   * corresponde ahí, pero eso lo decide el CSS y no un cambio de comportamiento. */

  let eligiendoCliente = false;
  /** Si el drawer es quien dice los errores del carrito. Lo fija `montarDrawer`. */
  let muestraErrores = false;

  function lineaHtml(l) {
    const c = carrito.cotizado.get(l.sku);
    const problema = c?.problema;
    return `
      <div class="linea ${problema ? "linea-mala" : ""}">
        <div class="que">
          <span class="desc">${escapar(l.descripcion)}</span>
          <small>de a ${l.multiplo_venta} · ${l.cantidad.toLocaleString("es-CL")} un.</small>
          ${problema ? `<small class="problema">${escapar(problema)}</small>` : ""}
          ${c?.cantidad_sugerida
            ? `<button type="button" class="plano arreglar" data-sku="${escapar(l.sku)}"
                 data-cantidad="${c.cantidad_sugerida}">Dejar ${c.cantidad_sugerida}</button>`
            : ""}
        </div>
        <div class="contador">
          <button type="button" class="plano menos" data-sku="${escapar(l.sku)}"
                  aria-label="Un envase menos">−</button>
          <b>${envasesDe(l)}</b>
          <button type="button" class="plano mas" data-sku="${escapar(l.sku)}"
                  aria-label="Un envase más">+</button>
        </div>
        <div class="plata">
          ${c && !problema ? pesos(c.total_linea_clp) : "—"}
          <button type="button" class="plano quitar" data-sku="${escapar(l.sku)}"
                  aria-label="Quitar">✕</button>
        </div>
      </div>`;
  }

  /** El selector de ferretería: sin esto «Agregar» no tiene a quién atribuirse. */
  function selectorHtml() {
    const abiertas = carrito.almacen.guardaVarias
      ? carrito.borradores.filter((b) => b.client_uuid !== carrito.lista?.client_uuid)
      : [];
    return `
      <div class="elegir-cliente">
        <label for="carrito-cliente">¿Para qué ferretería?</label>
        <input id="carrito-cliente" list="carrito-clientes" autocomplete="off"
               placeholder="Escribe la ferretería o el RUT">
        <datalist id="carrito-clientes">${[...carrito.cartera.values()]
          .map((c) => `<option value="${escapar(c.razon_social)} — ${escapar(c.rut)}"></option>`)
          .join("")}</datalist>
        <button type="button" id="carrito-empezar">Empezar la lista</button>
        ${abiertas.length
          ? `<p class="pista">O sigue una que ya tienes abierta:</p>
             <div class="abiertas">${abiertas.map((b) => `
               <button type="button" class="plano retomar" data-uuid="${escapar(b.client_uuid)}">
                 ${escapar(b.cliente_razon_social || b.cliente_rut)}
                 <small>${b.lineas.length} línea${b.lineas.length === 1 ? "" : "s"}</small>
               </button>`).join("")}</div>`
          : ""}
      </div>`;
  }

  function pintarDrawer() {
    const caja = document.getElementById("carrito");
    if (!caja) return;
    const cuerpo = document.getElementById("carrito-cuerpo");
    const pie = document.getElementById("carrito-pie");
    const boton = document.getElementById("carrito-boton");
    const lista = carrito.lista;
    const lineas = lista?.lineas || [];

    document.getElementById("carrito-para").innerHTML = lista
      ? `${escapar(lista.cliente_razon_social)} · ` +
        '<button type="button" class="plano cambiar" id="carrito-cambiar">cambiar</button>'
      : "Todavía no elegiste ferretería";

    // Quién muestra los errores se decide al montar, no por el orden en que se
    // suscribieron los oyentes: en el catálogo el drawer es la única superficie que
    // tiene el carrito, y en /pedido ya existe `#mensaje`, que es donde se miran.
    const aviso = document.getElementById("carrito-error");
    if (muestraErrores && carrito.error) {
      aviso.textContent = carrito.error;
      aviso.hidden = false;
      carrito.error = null;
    } else if (!eligiendoCliente) {
      aviso.hidden = true;
    }

    if (!lista || eligiendoCliente) cuerpo.innerHTML = selectorHtml();
    else if (!lineas.length)
      cuerpo.innerHTML =
        '<p class="pista vacia">Todavía no agregaste nada. Busca en el catálogo y aprieta ' +
        "«Agregar».</p>";
    else cuerpo.innerHTML = lineas.map(lineaHtml).join("");

    const hay = Boolean(lineas.length) && !eligiendoCliente;
    pie.hidden = !hay;
    if (hay) {
      document.getElementById("carrito-totales").innerHTML =
        `<span>Neto ${pesos(carrito.totales.neto_clp)} · IVA ${pesos(carrito.totales.iva_clp)}</span>
         <strong>${pesos(carrito.totales.total_clp)}</strong>`;
      const g = document.getElementById("carrito-guardado");
      g.textContent = carrito.estadoGuardado;
      g.className = `guardado ${carrito.claseGuardado || ""}`;
      document.getElementById("carrito-enviar").disabled = Boolean(carrito.totales.con_problema);
    }

    // El botón flotante sólo aparece con algo adentro —vacío sería un adorno que tapa— y
    // sólo con el panel minimizado: abierto, serían dos veces la misma cuenta.
    boton.hidden = !lineas.length || !caja.hidden;
    document.getElementById("carrito-cuenta").textContent = lineas.length;
    document.getElementById("carrito-monto").textContent = lineas.length
      ? pesos(carrito.totales.total_clp)
      : "";
  }

  /** Abre o minimiza el panel. Minimizar no pierde nada: la lista queda igual y el botón
   *  flotante la trae de vuelta. `con-carrito` en el body es lo que corre la hoja para
   *  que el panel no quede encima de la tabla. */
  const abrirDrawer = (abierto) => {
    const caja = document.getElementById("carrito");
    if (!caja) return;
    caja.hidden = !abierto;
    document.getElementById("carrito-fondo").hidden = !abierto;
    document.body.classList.toggle("con-carrito", abierto);
    document.getElementById("carrito-boton").setAttribute("aria-expanded", String(abierto));
    // También al minimizar: es cuando reaparece el botón flotante con la cuenta al día.
    pintarDrawer();
  };
  carrito.abrirDrawer = abrirDrawer;
  const drawerAbierto = () => {
    const caja = document.getElementById("carrito");
    return Boolean(caja) && !caja.hidden;
  };

  /** Engancha el drawer al DOM de la página. `alVerCompleta` es lo que hace «Ver
   *  completa»: en /pedido baja a la tarjeta grande, en el catálogo navega a /pedido. */
  carrito.montarDrawer = function (
    { alVerCompleta, alEnviar, errores = false, textoEnviar = "" } = {}
  ) {
    const caja = document.getElementById("carrito");
    if (!caja) return;
    muestraErrores = errores;

    document.getElementById("carrito-boton").onclick = () => abrirDrawer(caja.hidden);
    document.getElementById("carrito-minimizar").onclick = () => abrirDrawer(false);
    document.getElementById("carrito-fondo").onclick = () => abrirDrawer(false);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !caja.hidden) abrirDrawer(false);
    });

    document.getElementById("carrito-ver").onclick = () =>
      alVerCompleta ? alVerCompleta() : (location.href = "/pedido");

    // En el catálogo el botón lleva a revisar, porque enviar de verdad pide confirmar y
    // arreglar líneas, y eso vive en /pedido. Decir «Enviar» y navegar sería mentir.
    const enviarBoton = document.getElementById("carrito-enviar");
    if (textoEnviar) enviarBoton.textContent = textoEnviar;
    enviarBoton.onclick = () => alEnviar?.();

    caja.addEventListener("click", async (e) => {
      const boton = e.target.closest("button");
      if (!boton) return;
      const { sku, cantidad, uuid: cual } = boton.dataset;
      if (boton.classList.contains("menos")) return carrito.mover(sku, -1);
      if (boton.classList.contains("mas")) return carrito.mover(sku, 1);
      if (boton.classList.contains("quitar")) return carrito.quitar(sku);
      if (boton.classList.contains("arreglar")) return carrito.fijar(sku, cantidad);
      if (boton.id === "carrito-cambiar") {
        eligiendoCliente = true;
        return pintarDrawer();
      }
      if (boton.classList.contains("retomar")) {
        eligiendoCliente = false;
        return carrito.abrir(carrito.borradores.find((b) => b.client_uuid === cual));
      }
      if (boton.id === "carrito-empezar") {
        const cliente = carrito.resolverCliente(document.getElementById("carrito-cliente").value);
        if (!cliente) {
          carrito.error = "Elige una ferretería de la lista para empezar.";
          return avisarCambio();
        }
        boton.disabled = true;
        try {
          eligiendoCliente = false;
          await carrito.crear(cliente);
        } catch (err) {
          carrito.error = err.message;
          eligiendoCliente = true;
          avisarCambio();
        } finally {
          boton.disabled = false;
        }
      }
    });

    carrito.alCambiar(pintarDrawer);
    pintarDrawer();
  };

  /** Agrega desde el catálogo, donde puede no haber lista todavía.
   *
   *  Sin lista abierta no se pierde el producto ni se inventa un cliente: se abre el
   *  drawer en el selector y, apenas se elige la ferretería, entra lo que se quería
   *  agregar. Perder el clic obligaría a buscar el producto de nuevo.
   *
   *  Con lista abierta **no se abre el panel**. Abrirlo en cada «Agregar» tapaba la foto
   *  y la fila que el vendedor le está mostrando al ferretero, justo mientras dicta: el
   *  panel se abre cuando se quiere mirar la lista, no cuando se le suma algo. La
   *  confirmación la da el botón flotante, que late y trae la cuenta al día. */
  let pendiente = null;
  carrito.agregarDesdeCatalogo = async function (producto, cuantos = 1) {
    if (!carrito.lista) {
      pendiente = { producto, cuantos };
      eligiendoCliente = true;
      abrirDrawer(true);
      return null;
    }
    const linea = carrito.agregar(producto, cuantos);
    if (!drawerAbierto()) latir();
    return linea;
  };

  /** El latido del botón flotante: es el acuse de recibo cuando el panel está minimizado.
   *  Sin él, agregar con el panel cerrado no se ve por ninguna parte. */
  function latir() {
    const boton = document.getElementById("carrito-boton");
    if (!boton) return;
    boton.classList.remove("late");
    void boton.offsetWidth; // reinicia la animación si se agrega dos veces seguidas
    boton.classList.add("late");
  }

  carrito.alCambiar(() => {
    if (pendiente && carrito.lista && !eligiendoCliente) {
      const { producto, cuantos } = pendiente;
      pendiente = null;
      carrito.agregar(producto, cuantos);
    }
  });

  /** Deja el carrito listo en cualquier página: elige dónde se guarda según el rol,
   *  carga la cartera y retoma la lista activa —la que quedó abierta en la otra página—. */
  carrito.montar = async function (usuario) {
    carrito.almacen = usuario.rol === "vendedor" ? enServidor : enNavegador;
    carrito.montado = true;
    await carrito.cargarCartera();
    await carrito.refrescarBorradores();

    const activa = sessionStorage.getItem(CLAVE_ACTIVA);
    const retomar = carrito.almacen.guardaVarias
      ? carrito.borradores.find((b) => b.client_uuid === activa)
      : carrito.borradores[0];
    if (retomar) carrito.abrir(retomar);
    else avisarCambio();
    return carrito;
  };

  return {
    pedir, ingresar, yo, salir, descargar, descargarGrande,
    pesos, escapar, oVacio, avisar, aplazar, porWhatsapp, proteger, uuid,
    pintarSesion, urlDeIngreso, rutaLocal, alcanza, PAGINAS, quienSoy,
    haySesion: () => Boolean(token()),
    carrito,
  };
})();
