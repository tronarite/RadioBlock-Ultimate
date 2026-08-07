let radiosCache = {};

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

  tbody.innerHTML = radios
    .map((r) => `
      <tr>
        <td><a class="radio-link" href="/radio/${r.id}">${r.nombre}</a><br><span class="muted">${r.descripcion ?? ""}</span></td>
        <td>${r.activa ? (r.connected ? '<span class="badge musica">conectada</span>' : '<span class="badge caido">activa, sin conectar</span>') : '<span class="badge silencio">inactiva</span>'}</td>
        <td>${r.activa ? `<span class="badge ${r.state}">${fmtLabel(r.state)}</span>` : "—"}</td>
        <td>${r.pending_count ?? 0}</td>
        <td>${r.proxy_port ? `<code>:${r.proxy_port}</code>` : "—"}</td>
        <td class="row">
          ${r.activa
            ? `<button class="secondary" onclick="toggleRadio(${r.id}, false)">Desactivar</button>`
            : `<button onclick="toggleRadio(${r.id}, true)">Activar</button>`}
          <button class="danger" onclick="deleteRadio(${r.id})">Eliminar</button>
        </td>
      </tr>
    `)
    .join("");
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
    render();
  }
});

loadRadios();
