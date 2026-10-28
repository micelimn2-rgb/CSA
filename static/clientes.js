"use strict";
// Clientes: buscador sobre el Excel de deuda y registro de acciones compartido.
// Usa $, api, fmt, esc y fmtFecha de app.js.

const crm = { cargado: false, fichaId: null, tBuscar: null };

const fmtFechaHora = (s) => fmtFecha(s) + (s && s.length > 10 ? " " + s.slice(11, 16) : "");

function recordarAutor(nombre) {
  try { localStorage.setItem("csa-autor", nombre); } catch {}
}
function autorRecordado() {
  try { return localStorage.getItem("csa-autor") || ""; } catch { return ""; }
}

function cargarClientes() {
  if (!crm.cargado) buscarClientes();
}

// ------------------------------------------------------------ Buscador

async function buscarClientes() {
  const lista = $("#c-lista");
  try {
    const r = await api("api/clientes?q=" + encodeURIComponent($("#c-q").value.trim()));
    crm.cargado = true;
    $("#c-estado").textContent = r.total_clientes
      ? `${r.total_clientes} clientes · último Excel importado: corte del ${fmtFecha(r.ultimo_corte)}`
      : "Importá el Excel semanal de deuda para empezar.";
    lista.replaceChildren(...r.clientes.map(itemCliente));
    if (!r.clientes.length) {
      lista.innerHTML = `<p class="vacio">${r.total_clientes ? "Ningún cliente coincide con la búsqueda." : "Todavía no hay clientes cargados."}</p>`;
    }
  } catch (err) {
    lista.innerHTML = `<p class="vacio">${esc(err.message)}</p>`;
  }
}

function itemCliente(c) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "item cli" + (c.en_ultimo_corte ? "" : " fuera");
  const vencida = Object.entries(c.tramos).filter(([t, v]) => v && !t.startsWith("0-"));
  const meta = [c.vendedor && `Vendedor: ${c.vendedor}`, c.fecha && `corte ${fmtFecha(c.fecha)}`,
    ...vencida.map(([t, v]) => `${t} días: ${fmt(v)}`)].filter(Boolean);
  const chips = [];
  if (c.comentarios) chips.push(`<span class="chip">💬 ${c.comentarios}</span>`);
  if (!c.en_ultimo_corte) chips.push(`<span class="chip">no figura en el último Excel</span>`);
  b.innerHTML = `
    <span class="titulo">${esc(c.nombre)}</span>
    <span class="monto">${c.total != null ? fmt(c.total) : ""}</span>
    <span class="meta">${esc(meta.join(" · "))}</span>
    <span class="chips">${chips.join(" ")}</span>`;
  b.onclick = () => abrirFicha(c.id);
  return b;
}

$("#c-q").addEventListener("input", () => {
  cerrarFicha();
  clearTimeout(crm.tBuscar);
  crm.tBuscar = setTimeout(buscarClientes, 200);
});

// ------------------------------------------------------------ Importar Excel

$("#c-excel").addEventListener("change", async (ev) => {
  const archivo = ev.target.files[0];
  ev.target.value = "";
  if (!archivo) return;
  const res = $("#c-import-res");
  res.hidden = false;
  res.className = "nota";
  res.textContent = `Importando ${archivo.name}…`;
  const fd = new FormData();
  fd.append("archivo", archivo);
  try {
    const r = await api("api/clientes/importar", { method: "POST", body: fd });
    res.className = "nota ok";
    res.textContent = `✓ ${r.filas} filas importadas: ${r.clientes_nuevos} clientes nuevos, ` +
      `${r.comentarios_nuevos} comentarios nuevos al historial.`;
    cerrarFicha();
    buscarClientes();
  } catch (err) {
    res.className = "nota aviso";
    res.textContent = err.message;
  }
});

// ------------------------------------------------------------ Ficha del cliente

async function abrirFicha(id) {
  crm.fichaId = id;
  try {
    pintarFicha(await api(`api/clientes/${id}`));
    $("#c-lista").hidden = true;
    $("#c-ficha").hidden = false;
    $("#c-autor").value ||= autorRecordado();
    window.scrollTo({ top: 0 });
  } catch (err) {
    alert(err.message);
  }
}

function cerrarFicha() {
  crm.fichaId = null;
  $("#c-ficha").hidden = true;
  $("#c-lista").hidden = false;
  $("#c-error").hidden = true;
}

function pintarFicha(c) {
  $("#c-nombre").textContent = c.nombre;
  $("#c-sub").textContent = [c.vendedor && `Vendedor: ${c.vendedor}`, c.fecha && `Deuda al ${fmtFecha(c.fecha)}`]
    .filter(Boolean).join(" · ");

  const tramos = $("#c-tramos");
  tramos.innerHTML = Object.entries(c.tramos).map(([t, v]) =>
    `<div class="kpi"><h3>${esc(t)} días</h3><div class="valor">${fmt(v)}</div></div>`).join("") +
    `<div class="kpi total"><h3>Total</h3><div class="valor">${fmt(c.total || 0)}</div></div>`;
  $(".aviso-fuera")?.remove();
  if (!c.en_ultimo_corte && c.fecha) {
    tramos.insertAdjacentHTML("afterend",
      `<p class="aviso-fuera">No figura en el último Excel importado: la última deuda registrada es del ${fmtFecha(c.fecha)}.</p>`);
  }

  const hist = $("#c-historial");
  hist.innerHTML = c.historial.length
    ? c.historial.map((h) => `
      <li class="${h.origen}">
        <div class="h-meta"><b>${esc(h.autor)}</b> · ${fmtFechaHora(h.fecha)}${h.origen === "excel" ? " · del Excel" : ""}</div>
        <div class="h-txt">${esc(h.texto)}</div>
      </li>`).join("")
    : `<li class="vacio nota">Sin acciones registradas todavía.</li>`;

  const nombresTramos = Object.keys(c.tramos);
  $("#c-cortes").innerHTML = `
    <thead><tr><th>Fecha</th>${nombresTramos.map((t) => `<th>${esc(t)}</th>`).join("")}<th>Total</th><th>Vendedor</th></tr></thead>
    <tbody>${c.cortes.map((k) => `<tr><td>${fmtFecha(k.fecha)}</td>${nombresTramos.map((t) =>
      `<td class="${k.tramos[t] ? "" : "cero-v"}">${fmt(k.tramos[t] || 0)}</td>`).join("")}<td><b>${fmt(k.total)}</b></td><td>${esc(k.vendedor || "")}</td></tr>`).join("")}</tbody>`;
}

$("#c-volver").onclick = () => { cerrarFicha(); buscarClientes(); };

$("#c-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const autor = $("#c-autor").value.trim();
  const texto = $("#c-texto").value.trim();
  const error = $("#c-error");
  error.hidden = true;
  if (!autor || !texto) {
    error.textContent = !autor ? "Poné tu nombre para que se sepa quién hizo la acción." : "Escribí el comentario.";
    error.hidden = false;
    (!autor ? $("#c-autor") : $("#c-texto")).focus();
    return;
  }
  const boton = ev.submitter || $("#c-form button[type=submit]");
  boton.disabled = true;
  try {
    await api(`api/clientes/${crm.fichaId}/comentarios`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ autor, texto }),
    });
    recordarAutor(autor);
    $("#c-texto").value = "";
    pintarFicha(await api(`api/clientes/${crm.fichaId}`));
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  } finally {
    boton.disabled = false;
  }
});

// Lo que cargan los demás aparece solo mientras la ficha está abierta
setInterval(async () => {
  if (crm.fichaId == null || document.hidden || $("#vista-clientes").hidden || $("#c-texto").value) return;
  try { pintarFicha(await api(`api/clientes/${crm.fichaId}`)); } catch {}
}, 30000);
