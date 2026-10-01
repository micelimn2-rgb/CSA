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

async function api(url, opciones = {}) {
  const r = await fetch(url, opciones);
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
  $("#btn-csv").href = "api/exportar.csv" + (qs ? "?" + qs : "");
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
        <span class="chips">${m.tiene_adjunto ? '<span class="chip">📎 factura</span>' : m.tiene_factura ? '<span class="chip">con factura</span>' : '<span class="chip">manual</span>'}</span>
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
  setEstadoAdjunto("Sin factura: cargá el gasto manualmente.");
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
    const datos = await api("api/leer-factura", { method: "POST", body: fd });
    const campos = ["monto", "moneda", "fecha_factura", "numero_factura", "tercero", "cuit", "descripcion"];
    let n = 0;
    for (const c of campos) {
      if (datos[c] == null || !form[c]) continue;
      if (c === "moneda" && ![...form.moneda.options].some((o) => o.value === datos[c])) {
        form.moneda.add(new Option(datos[c]));
      }
      form[c].value = datos[c];
      n++;
    }
    if (n) setEstadoAdjunto(`📄 ${archivo.name} — se completaron ${n} campos. Revisalos antes de guardar.`, "ok");
    else setEstadoAdjunto(`📄 ${archivo.name} — ${datos.aviso || "no se pudieron leer datos"}. Completá los campos a mano.`, "aviso");
  } catch (e) {
    setEstadoAdjunto(`📄 ${archivo.name} — ${e.message}`, "aviso");
    if (/acepta|supera|vacío/.test(e.message)) estado.archivo = null;
  }
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

cargar().catch((e) => ($("#lista").innerHTML = `<p class="vacio">No se pudo conectar: ${esc(e.message)}</p>`));
cargarCategorias().catch(() => {});
