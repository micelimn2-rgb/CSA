"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

const estado = { tipo: "", editandoId: null, archivo: null, quitarAdjunto: false };
const dlg = $("#dlg");
const form = $("#form");

const hoy = () => new Date().toISOString().slice(0, 10);
const fmt = (n, moneda = "ARS") =>
  new Intl.NumberFormat("es-AR", { style: "currency", currency: moneda }).format(n);
const fmtFecha = (iso) => (iso ? iso.slice(0, 10).split("-").reverse().join("/") : "");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ------------------------------------------------------------ Versión
// Si se actualizan los archivos con la app abierta, el servidor sigue con el código viejo.
const VERSION_WEB = "5";

async function verificarVersion() {
  let version = null;
  try {
    const r = await fetch("api/version", { cache: "no-store" });
    if (r.ok) version = (await r.json()).version;
  } catch {}
  if (version === VERSION_WEB) return;
  const aviso = document.createElement("div");
  aviso.className = "aviso-version";
  aviso.setAttribute("role", "alert");
  aviso.innerHTML = version === null && !navigator.onLine
    ? "No hay conexión con la app."
    : "<b>La app se actualizó, pero el servidor sigue con la versión anterior.</b> " +
      "Cerrá <b>todas</b> las ventanas negras de la app y volvé a abrir <b>iniciar.bat</b>. Después recargá esta página.";
  document.body.prepend(aviso);
}

// ------------------------------------------------------------ Empresa activa
// Todo lo que se ve y se carga corresponde a la empresa elegida arriba.

const empresas = { id: null, lista: [] };
const empresaActual = () => empresas.lista.find((e) => e.id === empresas.id);

function conEmpresa(url) {
  if (!url.startsWith("api/") || url.startsWith("api/empresas") || empresas.id == null) return url;
  return url + (url.includes("?") ? "&" : "?") + "empresa=" + empresas.id;
}

function recordarEmpresa(id) {
  try { localStorage.setItem("csa-empresa", String(id)); } catch {}
}
function empresaRecordada() {
  try { return Number(localStorage.getItem("csa-empresa")) || null; } catch { return null; }
}

async function cargarEmpresas() {
  empresas.lista = await api("api/empresas");
  if (!empresas.lista.some((e) => e.id === empresas.id)) {
    const rec = empresaRecordada();
    empresas.id = empresas.lista.some((e) => e.id === rec) ? rec : empresas.lista[0].id;
  }
  pintarEmpresas();
}

function pintarEmpresas() {
  const cont = $("#empresas");
  cont.replaceChildren();
  for (const e of empresas.lista) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "empresa" + (e.id === empresas.id ? " activa" : "");
    b.setAttribute("aria-pressed", e.id === empresas.id);
    b.textContent = e.nombre;
    b.onclick = () => elegirEmpresa(e.id);
    cont.appendChild(b);
  }
  const sel = form.empresa_id;
  sel.replaceChildren(...empresas.lista.map((e) => new Option(e.nombre, e.id)));
  const actual = empresaActual();
  document.title = `${actual.nombre} · CSA Ingresos y Gastos`;
  $$(".nombre-empresa").forEach((el) => (el.textContent = actual.nombre));
}

function elegirEmpresa(id) {
  if (id === empresas.id) return;
  empresas.id = id;
  recordarEmpresa(id);
  pintarEmpresas();
  document.dispatchEvent(new Event("empresa-cambiada"));
}

$("#btn-empresa-nueva").onclick = async () => {
  const nombre = prompt("Nombre de la nueva empresa:");
  if (!nombre?.trim()) return;
  try {
    const e = await api("api/empresas", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nombre }) });
    await cargarEmpresas();
    elegirEmpresa(e.id);
  } catch (err) { alert(err.message); }
};
$("#btn-empresa-renombrar").onclick = async () => {
  const actual = empresaActual();
  const nombre = prompt("Nuevo nombre para la empresa:", actual.nombre);
  if (!nombre?.trim() || nombre === actual.nombre) return;
  try {
    await api(`api/empresas/${actual.id}`, { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nombre }) });
    await cargarEmpresas();
  } catch (err) { alert(err.message); }
};

async function api(url, opciones = {}) {
  const r = await fetch(conEmpresa(url), opciones);
  if (!r.ok) {
    let msg = `Error ${r.status}`;
    try {
      const d = await r.json();
      msg = typeof d.detail === "string" ? d.detail : (d.detail || []).map((e) => e.msg).join(", ") || msg;
    } catch {}
    throw new Error(msg);
  }
  return r.status === 204 ? null : r.json();
}

function filtrosQS() {
  const p = new URLSearchParams();
  if (estado.tipo) p.set("tipo", estado.tipo);
  const q = $("#f-q").value.trim();
  if (q) p.set("q", q);
  if ($("#f-desde").value) p.set("desde", $("#f-desde").value);
  if ($("#f-hasta").value) p.set("hasta", $("#f-hasta").value);
  return p.toString();
}

// ------------------------------------------------------------ Listado y resumen

async function cargar() {
  const qs = filtrosQS();
  $("#btn-csv").href = conEmpresa("api/exportar.csv" + (qs ? "?" + qs : ""));
  const [movs, res] = await Promise.all([api("api/movimientos?" + qs), api("api/resumen?" + qs)]);
  pintarResumen(res);
  pintarLista(movs);
}

function pintarResumen(res) {
  const monedas = Object.keys(res);
  if (!monedas.length) monedas.push("ARS"), (res.ARS = { ingresos: 0, egresos: 0, saldo: 0, cantidad: 0 });
  $("#resumen").innerHTML = monedas
    .map((m) => {
      const r = res[m];
      return `<div class="tarjeta">
        <h3>${esc(m)} · ${r.cantidad} movimientos</h3>
        <div class="fila"><span>Ingresos</span><span class="ing">${fmt(r.ingresos, m)}</span></div>
        <div class="fila"><span>Egresos</span><span class="egr">${fmt(r.egresos, m)}</span></div>
        <div class="fila saldo"><span>Saldo</span><span class="${r.saldo >= 0 ? "ing" : "egr"}">${fmt(r.saldo, m)}</span></div>
      </div>`;
    })
    .join("");
}

function pintarLista(movs) {
  const lista = $("#lista");
  if (!movs.length) {
    lista.innerHTML = `<p class="vacio">No hay movimientos. Tocá <b>+ Nuevo</b> para cargar el primero.</p>`;
    return;
  }
  lista.innerHTML = movs
    .map(
      (m) => `<button class="item ${m.tipo}" data-id="${m.id}">
        <span class="titulo">${esc(m.tercero || m.descripcion || (m.tipo === "ingreso" ? "Ingreso" : "Gasto"))}</span>
        <span class="monto ${m.tipo === "ingreso" ? "ing" : "egr"}">${m.tipo === "ingreso" ? "+" : "−"} ${fmt(m.monto, m.moneda)}</span>
        <span class="meta">${fmtFecha(m.fecha_factura)}${m.categoria ? " · " + esc(m.categoria) : ""}${m.numero_factura ? " · " + esc(m.numero_factura) : ""}</span>
        <span class="chips">${m.revisar ? '<span class="chip pendiente">⚡ a revisar</span> ' : ""}${m.tiene_adjunto ? '<span class="chip">📎 comprobante</span>' : m.tiene_factura ? '<span class="chip">con factura</span>' : '<span class="chip">manual</span>'}</span>
      </button>`
    )
    .join("");
}

async function cargarCategorias() {
  const cats = await api("api/categorias");
  $("#categorias").innerHTML = cats.map((c) => `<option value="${esc(c)}">`).join("");
}

// ------------------------------------------------------------ Formulario

function setTipo(tipo) {
  form.tipo.value = tipo;
  $$(".seg.grande button").forEach((b) => b.classList.toggle("activo", b.dataset.valor === tipo));
}

function setEstadoAdjunto(texto, clase = "") {
  const el = $("#adj-estado");
  el.textContent = texto;
  el.className = "nota " + clase;
}

function abrir(mov = null) {
  form.reset();
  estado.editandoId = mov?.id ?? null;
  estado.archivo = null;
  estado.quitarAdjunto = false;
  $("#form-error").hidden = true;
  $("#dlg-titulo").textContent = mov ? "Editar movimiento" : "Nuevo movimiento";
  $("#btn-borrar").hidden = !mov;
  setTipo(mov?.tipo || "egreso");
  form.fecha_factura.value = mov?.fecha_factura || hoy();
  form.empresa_id.value = mov?.empresa_id ?? empresas.id;
  $("#fecha-carga").value = mov ? mov.fecha_carga : "Hoy (automática)";
  for (const campo of ["monto", "moneda", "tercero", "cuit", "numero_factura", "categoria", "descripcion"]) {
    if (mov && mov[campo] != null) form[campo].value = mov[campo];
  }
  const actual = $("#adj-actual");
  if (mov?.tiene_adjunto) {
    actual.hidden = false;
    actual.innerHTML = `Adjunto: <a href="api/movimientos/${mov.id}/adjunto" target="_blank" rel="noopener">${esc(mov.adjunto_nombre)}</a>
      · <button type="button" class="btn ghost" id="btn-quitar">Quitar</button>`;
    $("#btn-quitar").onclick = () => {
      estado.quitarAdjunto = true;
      actual.hidden = true;
      setEstadoAdjunto("El adjunto se quitará al guardar.");
    };
  } else {
    actual.hidden = true;
  }
  setEstadoAdjunto(mov?.tiene_adjunto ? "Podés reemplazar el comprobante adjuntando otro."
    : "Sin factura: cargá el gasto manualmente.");
  dlg.showModal();
  if (!mov) form.monto.focus();
}

async function alElegirArchivo(ev) {
  const archivo = ev.target.files[0];
  ev.target.value = "";
  if (!archivo) return;
  estado.archivo = archivo;
  setEstadoAdjunto(`📄 ${archivo.name} — leyendo datos de la factura…`);
  const fd = new FormData();
  fd.append("archivo", archivo);
  try {
    aplicarDatos(await api("api/leer-factura", { method: "POST", body: fd }), archivo);
  } catch (e) {
    setEstadoAdjunto(`📄 ${archivo.name} — ${e.message}`, "aviso");
    if (/acepta|supera|vacío/.test(e.message)) estado.archivo = null;
  }
}

// Vuelca en el formulario los datos leídos de un comprobante
function aplicarDatos(datos, archivo) {
  const campos = ["monto", "moneda", "fecha_factura", "numero_factura", "tercero", "cuit", "categoria", "descripcion"];
  let n = 0;
  for (const c of campos) {
    if (datos[c] == null || !form[c]) continue;
    if (c === "moneda" && ![...form.moneda.options].some((o) => o.value === datos[c])) {
      form.moneda.add(new Option(datos[c]));
    }
    form[c].value = datos[c];
    n++;
  }
  if (datos.tipo) setTipo(datos.tipo);
  if (datos.empresa_id) form.empresa_id.value = datos.empresa_id;
  if (n) setEstadoAdjunto(`📄 ${archivo.name} — se completaron ${n} campos. Revisalos antes de guardar.`, "ok");
  else setEstadoAdjunto(`📄 ${archivo.name} — ${datos.aviso || "no se pudieron leer datos"}. Completá los campos a mano.`, "aviso");
}

async function guardar(ev) {
  ev.preventDefault();
  const err = $("#form-error");
  err.hidden = true;
  if (!form.monto.value || Number(form.monto.value) < 0) {
    err.textContent = "Ingresá un monto válido.";
    err.hidden = false;
    return;
  }
  const fd = new FormData(form);
  if (estado.archivo) fd.append("archivo", estado.archivo);
  if (estado.quitarAdjunto) fd.append("quitar_adjunto", "true");
  const btn = $("#btn-guardar");
  btn.disabled = true;
  try {
    const url = estado.editandoId ? `api/movimientos/${estado.editandoId}` : "api/movimientos";
    await api(url, { method: estado.editandoId ? "PUT" : "POST", body: fd });
    dlg.close();
    document.dispatchEvent(new Event("movimientos-cambiaron"));
    await Promise.all([cargar(), cargarCategorias()]);
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
  }
}

async function borrar() {
  if (!estado.editandoId || !confirm("¿Eliminar este movimiento y su factura adjunta?")) return;
  await api(`api/movimientos/${estado.editandoId}`, { method: "DELETE" });
  dlg.close();
  document.dispatchEvent(new Event("movimientos-cambiaron"));
  cargar();
}

// ------------------------------------------------------------ Eventos

$("#btn-nuevo").onclick = () => abrir();
$("#lista").onclick = async (ev) => {
  const item = ev.target.closest(".item");
  if (item) abrir(await api(`api/movimientos/${item.dataset.id}`));
};
$$(".filtros .seg button").forEach((b) =>
  b.addEventListener("click", () => {
    estado.tipo = b.dataset.tipo;
    $$(".filtros .seg button").forEach((x) => x.classList.toggle("activo", x === b));
    cargar();
  })
);
let tBusqueda;
$("#f-q").addEventListener("input", () => {
  clearTimeout(tBusqueda);
  tBusqueda = setTimeout(cargar, 250);
});
$("#f-desde").addEventListener("change", cargar);
$("#f-hasta").addEventListener("change", cargar);
$$(".seg.grande button").forEach((b) => b.addEventListener("click", () => setTipo(b.dataset.valor)));
$$("[data-cerrar]").forEach((b) => b.addEventListener("click", () => dlg.close()));
$("#in-archivo").addEventListener("change", alElegirArchivo);
$("#in-foto").addEventListener("change", alElegirArchivo);
$("#btn-borrar").onclick = borrar;
form.addEventListener("submit", guardar);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});

document.addEventListener("empresa-cambiada", () => {
  cargar().catch(() => {});
  cargarCategorias().catch(() => {});
});

// Las demás pantallas esperan a saber qué empresa mostrar
const listo = verificarVersion()
  .then(cargarEmpresas)
  .then(() => Promise.all([cargar(), cargarCategorias()]))
  .catch((e) => ($("#lista").innerHTML = `<p class="vacio">No se pudo conectar: ${esc(e.message)}</p>`));
