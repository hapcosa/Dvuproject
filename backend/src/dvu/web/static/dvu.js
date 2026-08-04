/* Cliente mínimo de la API DVU.
 *
 * Sin framework y sin build: el prototipo tiene que abrirse en el navegador de
 * cualquiera sin instalar nada. Todo lo que hace pasa por los mismos endpoints
 * documentados en /docs — la web no tiene atajos propios contra la base.
 *
 * El token va en sessionStorage y no en localStorage: se borra al cerrar la pestaña.
 * En un equipo compartido de bodega esa diferencia importa.
 */

const DVU = (() => {
  const base = document.querySelector('meta[name="dvu-api"]').content;
  const CLAVE = "dvu_token";

  const token = () => sessionStorage.getItem(CLAVE);

  async function pedir(ruta, opciones = {}) {
    const cabeceras = { ...(opciones.headers || {}) };
    if (token()) cabeceras.Authorization = `Bearer ${token()}`;
    if (opciones.body && !(opciones.body instanceof FormData)) {
      cabeceras["Content-Type"] = "application/json";
      opciones.body = JSON.stringify(opciones.body);
    }

    const respuesta = await fetch(base + ruta, { ...opciones, headers: cabeceras });

    if (respuesta.status === 401) {
      sessionStorage.removeItem(CLAVE);
      throw new Error("La sesión venció. Ingresa de nuevo.");
    }
    if (!respuesta.ok) {
      // El detalle del backend es el mensaje útil (qué falta, qué chocó). Se muestra
      // tal cual en vez de un "error 422" que no le dice nada a nadie.
      let detalle = `Error ${respuesta.status}`;
      try {
        const cuerpo = await respuesta.json();
        if (typeof cuerpo.detail === "string") detalle = cuerpo.detail;
        else if (Array.isArray(cuerpo.detail)) {
          detalle = cuerpo.detail.map((e) => `${e.loc?.slice(1).join(".")}: ${e.msg}`).join(" · ");
        }
      } catch { /* respuesta sin JSON: queda el código */ }
      throw new Error(detalle);
    }
    if (respuesta.status === 204) return null;
    const tipo = respuesta.headers.get("content-type") || "";
    return tipo.includes("json") ? respuesta.json() : respuesta.blob();
  }

  async function ingresar(email, password) {
    const datos = await pedir("/auth/login", { method: "POST", body: { email, password } });
    sessionStorage.setItem(CLAVE, datos.access_token);
    return yo();
  }

  const yo = () => pedir("/auth/yo");
  const salir = () => { sessionStorage.removeItem(CLAVE); location.reload(); };

  async function descargar(ruta, nombre) {
    const blob = await pedir(ruta);
    const url = URL.createObjectURL(blob);
    const enlace = Object.assign(document.createElement("a"), { href: url, download: nombre });
    document.body.appendChild(enlace);
    enlace.click();
    enlace.remove();
    URL.revokeObjectURL(url);
  }

  /* --- utilidades de presentación --------------------------------------- */

  // CLP sin decimales, siempre. El peso no tiene centavos.
  const pesos = (n) =>
    n === null || n === undefined ? "" : "$ " + Number(n).toLocaleString("es-CL");

  const escapar = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
    );

  /** Dato que el catálogo original no trae: se marca, no se inventa. */
  const oVacio = (valor) =>
    valor ? escapar(valor) : '<span class="vacio">—</span>';

  function avisar(elemento, mensaje, tipo = "info") {
    elemento.className = `aviso ${tipo}`;
    elemento.textContent = mensaje;
    elemento.hidden = false;
  }

  /** Muestra el bloque que corresponde al rol y deja la sesión visible arriba. */
  async function proteger({ rol, alIngresar }) {
    const login = document.getElementById("login");
    const app = document.getElementById("app");
    const sesion = document.getElementById("sesion");

    const mostrar = (usuario) => {
      login.hidden = true;
      app.hidden = false;
      sesion.innerHTML =
        `${escapar(usuario.nombre)} (${escapar(usuario.rol)}) · ` +
        '<a href="#" id="salir">salir</a>';
      document.getElementById("salir").onclick = (e) => { e.preventDefault(); salir(); };
      alIngresar?.(usuario);
    };

    if (token()) {
      try {
        const usuario = await yo();
        if (!rol || usuario.rol === "admin" || rol.includes(usuario.rol)) return mostrar(usuario);
        avisar(document.getElementById("login-error"),
          `Tu rol (${usuario.rol}) no tiene acceso a esta página.`, "error");
      } catch { sessionStorage.removeItem(CLAVE); }
    }

    login.hidden = false;
    app.hidden = true;
    document.getElementById("form-login").onsubmit = async (evento) => {
      evento.preventDefault();
      const error = document.getElementById("login-error");
      error.hidden = true;
      try {
        const usuario = await ingresar(evento.target.email.value, evento.target.password.value);
        if (rol && usuario.rol !== "admin" && !rol.includes(usuario.rol)) {
          return avisar(error, `Tu rol (${usuario.rol}) no tiene acceso a esta página.`, "error");
        }
        mostrar(usuario);
      } catch (e) {
        avisar(error, e.message, "error");
      }
    };
  }

  return { pedir, ingresar, yo, salir, descargar, pesos, escapar, oVacio, avisar, proteger };
})();
