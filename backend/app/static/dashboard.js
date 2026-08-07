let radiosCache = {};
let editingId = null;

async function loadRadios() {
  const radios = await api("/api/radios");
  radiosCache = Object.fromEntries(radios.map((r) => [r.id, r]));
  render();
}

function render() {
  const tbody = document.getElementById("radios-body");
  const radios = Object.values(radiosCache);
  if (radios.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty">No hay emisoras registradas todavía.</td></tr>`;
    return;
  }

  tbody.innerHTML = radios.map((r) => (r.id === editingId ? renderEditRow(r) : renderRow(r))).join("");
}

function renderRow(r) {
  return `
      <tr>
        <td><a class="radio-link" href="/radio/${r.id}">${escapeHtml(r.nombre)}</a><br><span class="muted">${escapeHtml(r.descripcion ?? "")}</span></td>
        <td>${r.activa ? (r.connected ? '<span class="badge musica">conectada</span>' : '<span class="badge caido">activa, sin conectar</span>') : '<span class="badge silencio">inactiva</span>'}</td>
        <td>${r.activa ? `<span class="badge ${r.state}">${fmtLabel(r.state)}</span>` : "—"}</td>
        <td>${r.pending_count ?? 0}</td>
        <td>${r.proxy_port ? `<code>:${r.proxy_port}</code>` : "—"}</td>
        <td class="row">
          ${r.activa
            ? `<button class="secondary" onclick="toggleRadio(${r.id}, false)">Desactivar</button>`
            : `<button onclick="toggleRadio(${r.id}, true)">Activar</button>`}
          <button class="secondary" onclick="empezarEdicion(${r.id})">Editar</button>
          <button class="danger" onclick="deleteRadio(${r.id})">Eliminar</button>
        </td>
      </tr>
    `;
}

function renderEditRow(r) {
  return `
      <tr>
        <td colspan="6">
          <div class="row" style="flex-wrap:wrap;">
            <input id="edit-nombre-${r.id}" value="${escapeHtml(r.nombre)}" placeholder="Nombre">
            <input id="edit-url-${r.id}" value="${escapeHtml(r.url)}" placeholder="URL del stream" style="flex:1; min-width:220px;">
            <input id="edit-descripcion-${r.id}" value="${escapeHtml(r.descripcion ?? "")}" placeholder="Descripción">
            <label class="muted" style="display:flex; align-items:center; gap:0.3rem;">
              Duración segmento (s)
              <input id="edit-duracion-${r.id}" type="number" min="5" step="1" value="${r.segment_duration_seconds}" style="width:70px;">
            </label>
            <label class="muted" style="display:flex; align-items:center; gap:0.3rem;">
              Umbral confianza
              <input id="edit-umbral-${r.id}" type="number" min="0" max="1" step="0.05" value="${r.confidence_threshold}" style="width:70px;">
            </label>
            <button onclick="guardarEdicion(${r.id})">Guardar</button>
            <button class="secondary" onclick="cancelarEdicion()">Cancelar</button>
          </div>
        </td>
      </tr>
    `;
}

function empezarEdicion(id) {
  editingId = id;
  render();
}

function cancelarEdicion() {
  editingId = null;
  render();
}

async function guardarEdicion(id) {
  const nombre = document.getElementById(`edit-nombre-${id}`).value.trim();
  const url = document.getElementById(`edit-url-${id}`).value.trim();
  const descripcion = document.getElementById(`edit-descripcion-${id}`).value.trim();
  const segment_duration_seconds = Number(document.getElementById(`edit-duracion-${id}`).value);
  const confidence_threshold = Number(document.getElementById(`edit-umbral-${id}`).value);

  if (!nombre || !url) {
    alert("Nombre y URL son obligatorios.");
    return;
  }

  await api(`/api/radios/${id}`, {
    method: "PATCH",
    body: JSON.stringify({
      nombre,
      url,
      descripcion: descripcion || null,
      segment_duration_seconds,
      confidence_threshold,
    }),
  });
  editingId = null;
  await loadRadios();
}

async function toggleRadio(id, activar) {
  await api(`/api/radios/${id}/${activar ? "activar" : "desactivar"}`, { method: "POST" });
  await loadRadios();
}

async function deleteRadio(id) {
  if (!confirm("¿Eliminar esta emisora y todo su historial?")) return;
  await api(`/api/radios/${id}`, { method: "DELETE" });
  await loadRadios();
}

document.getElementById("new-radio-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = new FormData(ev.target);
  await api("/api/radios", {
    method: "POST",
    body: JSON.stringify({
      nombre: form.get("nombre"),
      url: form.get("url"),
      descripcion: form.get("descripcion") || null,
    }),
  });
  ev.target.reset();
  await loadRadios();
});

connectWs((msg) => {
  if (msg.type === "radio_status" && radiosCache[msg.data.radio_id]) {
    Object.assign(radiosCache[msg.data.radio_id], msg.data);
    // Si se está editando esta radio ahora mismo, no se vuelve a pintar la
    // fila: renderizar de nuevo sustituiría los inputs y borraría en
    // silencio lo que el usuario esté escribiendo, antes de que pulse
    // "Guardar". El estado en vivo se actualiza igual en cuanto termine.
    if (msg.data.radio_id !== editingId) render();
  }
});

loadRadios();
