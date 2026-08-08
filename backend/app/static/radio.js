const radioId = Number(location.pathname.split("/").pop());
let radio = null;
let lastPendingCount = -1;

// -- tabs -----------------------------------------------------------------

document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "stats") loadStats();
    if (btn.dataset.tab === "patrones") loadClusters();
  });
});

// -- cabecera / estado -----------------------------------------------------------------

async function loadRadio() {
  radio = await api(`/api/radios/${radioId}`);
  document.getElementById("radio-name").textContent = radio.nombre;
  document.title = `${radio.nombre} — Radio Ad Blocker`;
  applyStatus(radio);
}

let liveAudioPort = null;

function applyStatus(status) {
  const badge = document.getElementById("estado-badge");
  badge.textContent = status.activa === false ? "inactiva" : fmtLabel(status.state);
  badge.className = `badge ${status.activa === false ? "silencio" : status.state}`;

  document.getElementById("proxy-url").textContent = status.proxy_port
    ? `proxy: ${location.hostname}:${status.proxy_port}`
    : "";

  const escuchaCard = document.getElementById("escucha-directo-card");
  const liveAudio = document.getElementById("live-audio");
  if (status.proxy_port && status.activa !== false) {
    escuchaCard.style.display = "";
    if (liveAudioPort !== status.proxy_port) {
      liveAudioPort = status.proxy_port;
      liveAudio.src = `http://${location.hostname}:${status.proxy_port}/`;
    }
  } else {
    escuchaCard.style.display = "none";
    liveAudio.removeAttribute("src");
    liveAudioPort = null;
  }

  // La corrección "no es anuncio" solo tiene sentido cuando el sistema
  // está silenciando algo AHORA MISMO por su cuenta (detección
  // automática) — y no mientras el usuario ya está marcando él mismo un
  // anuncio con "Empieza/Termina", para no mezclar los dos flujos.
  document.getElementById("correccion-row").style.display =
    status.state === "anuncio" && marcandoDesde === null ? "" : "none";

  const toggleBtn = document.getElementById("toggle-btn");
  if (radio) {
    toggleBtn.textContent = radio.activa ? "Desactivar" : "Activar";
    toggleBtn.onclick = async () => {
      await api(`/api/radios/${radioId}/${radio.activa ? "desactivar" : "activar"}`, { method: "POST" });
      await loadRadio();
    };
  }

  renderTunnel(status);

  if (typeof status.pending_count === "number" && status.pending_count !== lastPendingCount) {
    lastPendingCount = status.pending_count;
    loadPendientes();
  }
  if (status.activa !== false) pushEstado(status.state, status.connected);
}

// -- túnel público (Cloudflare) -----------------------------------------------------------------

let tunnelBusy = false;
let tunnelBusyAction = null; // "activando" | "desactivando"

function renderTunnel(status) {
  const row = document.getElementById("tunnel-row");
  const btn = document.getElementById("tunnel-btn");
  const info = document.getElementById("tunnel-info");

  if (status.activa === false) {
    row.style.display = "none";
    return;
  }
  row.style.display = "";

  if (tunnelBusy) {
    btn.textContent = "…";
    btn.disabled = true;
    info.textContent =
      tunnelBusyAction === "desactivando"
        ? "apagando el acceso público…"
        : "creando túnel público (puede tardar unos segundos)…";
    return;
  }
  btn.disabled = false;

  if (status.tunnel_state === "activo" && status.public_url) {
    btn.textContent = "Apagar acceso público";
    btn.onclick = desactivarTunnel;
    info.innerHTML = `pública en <a href="${status.public_url}" target="_blank" rel="noopener">${status.public_url}</a> <button class="secondary" style="padding:0.1rem 0.4rem;" onclick="copiarUrlTunnel()">copiar</button>`;
  } else {
    btn.textContent = "🌐 Exponer a internet (Cloudflare)";
    btn.onclick = activarTunnel;
    info.textContent =
      status.tunnel_state === "error"
        ? "no se pudo crear el túnel — revisa que cloudflared esté instalado"
        : "";
  }
}

async function activarTunnel() {
  tunnelBusy = true;
  tunnelBusyAction = "activando";
  renderTunnel(radio);
  try {
    const updated = await api(`/api/radios/${radioId}/tunnel/activar`, { method: "POST" });
    Object.assign(radio, updated);
  } catch (e) {
    alert("No se pudo activar el túnel: " + e.message);
  }
  tunnelBusy = false;
  renderTunnel(radio);
}

async function desactivarTunnel() {
  tunnelBusy = true;
  tunnelBusyAction = "desactivando";
  renderTunnel(radio);
  const updated = await api(`/api/radios/${radioId}/tunnel/desactivar`, { method: "POST" });
  Object.assign(radio, updated);
  tunnelBusy = false;
  renderTunnel(radio);
}

function copiarUrlTunnel() {
  if (radio && radio.public_url) navigator.clipboard.writeText(radio.public_url);
}

// -- línea de tiempo en vivo: qué fragmentos se van silenciando -----------------------------------------------------------------

const ESTADO_COLOR = {
  musica: "#35c47a",
  contenido: "#35c47a",
  anuncio: "#ef5c5c",
  silencio: "#8b909c",
  caido: "#8b909c",
};

const levelCtx = document.getElementById("level-chart");
const levelData = {
  labels: [],
  datasets: [{ data: [], backgroundColor: [], categoryPercentage: 1, barPercentage: 0.9 }],
};
const levelChart = new Chart(levelCtx, {
  type: "bar",
  data: levelData,
  options: {
    animation: false,
    scales: { x: { display: false }, y: { display: false, min: 0, max: 1 } },
    plugins: { legend: { display: false }, tooltip: { enabled: false } },
  },
});

let lastEstadoAt = 0;

function pushEstado(state, connected) {
  // Un fragmento nuevo llega cada `segment_duration_seconds`; evita repetir
  // la misma barra si llega más de una actualización de estado seguida sin
  // que haya pasado un fragmento nuevo de verdad.
  const now = Date.now();
  if (now - lastEstadoAt < 3000) return;
  lastEstadoAt = now;

  const color = connected === false ? ESTADO_COLOR.caido : (ESTADO_COLOR[state] || ESTADO_COLOR.silencio);
  levelData.labels.push(new Date().toLocaleTimeString());
  levelData.datasets[0].data.push(1);
  levelData.datasets[0].backgroundColor.push(color);
  if (levelData.labels.length > 90) {
    levelData.labels.shift();
    levelData.datasets[0].data.shift();
    levelData.datasets[0].backgroundColor.shift();
  }
  levelChart.update();
}

// -- patrones nuevos (clusters detectados por repetición, aún sin revisar) --------------------

async function loadPendientes() {
  const clusters = await api(`/api/radios/${radioId}/clusters?pendientes=true`);
  const el = document.getElementById("pendientes-list");
  if (clusters.length === 0) {
    el.innerHTML = `<p class="empty">Todavía no se ha detectado ningún patrón repetido. El sistema sigue escuchando.</p>`;
    return;
  }
  el.innerHTML = clusters
    .map(
      (c) => `
      <div class="row" style="justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid var(--border);">
        <span>Patrón repetido <span class="muted">(${c.n_apariciones} veces en momentos distintos, ${c.n_segmentos} segmentos en total)</span></span>
        ${c.representative_segment_id ? `<audio controls src="/api/segmentos/${c.representative_segment_id}/audio"></audio>` : "<span class=\"muted\">sin muestra</span>"}
        <span class="row">
          <button onclick="etiquetarCluster(${c.id}, 'anuncio')">Es anuncio</button>
          <button class="secondary" onclick="etiquetarCluster(${c.id}, 'contenido')">No es anuncio</button>
          <button class="secondary" onclick="etiquetarCluster(${c.id}, 'ignorado')">Ignorar</button>
        </span>
      </div>`
    )
    .join("");
}

async function etiquetarCluster(id, label) {
  await api(`/api/clusters/${id}`, { method: "PATCH", body: JSON.stringify({ label }) });
  await loadPendientes();
}

// -- marcado manual en directo -----------------------------------------------------------------

let marcandoDesde = null;
let marcandoTimer = null;

async function marcarActual(label) {
  const statusEl = document.getElementById("marcar-status");
  statusEl.textContent = "marcando…";
  try {
    await api(`/api/radios/${radioId}/marcar_actual`, {
      method: "POST",
      body: JSON.stringify({ label }),
    });
    statusEl.textContent = label === "anuncio" ? "marcado como anuncio ✓" : "marcado como contenido ✓";
  } catch (e) {
    statusEl.textContent = "error al marcar";
  }
  setTimeout(() => (statusEl.textContent = ""), 4000);
}

async function empezarMarcado() {
  const statusEl = document.getElementById("marcar-status");
  try {
    await api(`/api/radios/${radioId}/marcar_inicio`, {
      method: "POST",
      body: JSON.stringify({ label: "anuncio" }),
    });
  } catch (e) {
    statusEl.textContent = "error al empezar el marcado";
    return;
  }
  marcandoDesde = Date.now();
  document.getElementById("btn-marcar-inicio").style.display = "none";
  document.getElementById("btn-marcar-fin").style.display = "";
  marcandoTimer = setInterval(() => {
    const s = Math.floor((Date.now() - marcandoDesde) / 1000);
    statusEl.textContent = `🔴 marcando anuncio… ${s}s`;
  }, 500);
}

async function terminarMarcado() {
  const statusEl = document.getElementById("marcar-status");
  clearInterval(marcandoTimer);
  marcandoTimer = null;
  const duracion = marcandoDesde ? Math.round((Date.now() - marcandoDesde) / 1000) : null;
  marcandoDesde = null;
  document.getElementById("btn-marcar-fin").style.display = "none";
  document.getElementById("btn-marcar-inicio").style.display = "";
  try {
    await api(`/api/radios/${radioId}/marcar_fin`, { method: "POST" });
    statusEl.textContent = duracion ? `marcado como anuncio (${duracion}s) ✓` : "marcado como anuncio ✓";
  } catch (e) {
    statusEl.textContent = "error al terminar el marcado";
  }
  setTimeout(() => (statusEl.textContent = ""), 5000);
}

async function loadHistorial() {
  const list = await api(`/api/radios/${radioId}/segmentos/historial`);
  const tbody = document.getElementById("historial-body");
  tbody.innerHTML = list.length
    ? list
        .map((s) => `<tr><td>${new Date(s.timestamp + "Z").toLocaleString()}</td><td>${s.duracion}s</td></tr>`)
        .join("")
    : `<tr><td colspan="2" class="empty">Todavía no se ha mutado ningún segmento.</td></tr>`;
}

// -- estadísticas -----------------------------------------------------------------

let evolucionChart = null;

async function loadStats() {
  const s = await api(`/api/radios/${radioId}/stats`);
  document.getElementById("stats-grid").innerHTML = `
    <div class="stat"><div class="value">${s.minutos_escuchados}</div><div class="label">min. escuchados</div></div>
    <div class="stat"><div class="value">${s.minutos_mutados}</div><div class="label">min. mutados</div></div>
    <div class="stat"><div class="value">${s.porcentaje_anuncios}%</div><div class="label">tiempo en anuncios</div></div>
    <div class="stat"><div class="value">${s.n_patrones}</div><div class="label">patrones aprendidos</div></div>
  `;

  const ctx = document.getElementById("evolucion-chart");
  const labels = s.evolucion_diaria.map((d) => d.dia);
  const data = s.evolucion_diaria.map((d) => d.minutos_anuncio);
  if (evolucionChart) evolucionChart.destroy();
  evolucionChart = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ label: "min. anuncios", data, backgroundColor: "#ef5c5c" }] },
    options: { scales: { x: { ticks: { color: "#8b909c" } }, y: { ticks: { color: "#8b909c" } } } },
  });
}

// -- patrones / clusters -----------------------------------------------------------------

async function loadClusters() {
  const clusters = await api(`/api/radios/${radioId}/clusters`);
  const el = document.getElementById("clusters-list");
  if (clusters.length === 0) {
    el.innerHTML = `<p class="empty">Todavía no hay patrones aprendidos.</p>`;
    return;
  }
  el.innerHTML = clusters
    .map(
      (c) => `
      <div class="row" style="justify-content: space-between; padding: 0.6rem 0; border-bottom: 1px solid var(--border);">
        <span>Patrón #${c.id} <span class="muted">(${c.n_apariciones} apariciones, ${c.n_segmentos} segmentos)</span></span>
        ${c.representative_segment_id ? `<audio controls src="/api/segmentos/${c.representative_segment_id}/audio"></audio>` : "<span></span>"}
        <span class="row">
          <select id="label-${c.id}">
            <option value="" ${!c.label ? "selected" : ""}>sin revisar</option>
            <option value="anuncio" ${c.label === "anuncio" ? "selected" : ""}>Anuncio</option>
            <option value="contenido" ${c.label === "contenido" ? "selected" : ""}>No es anuncio</option>
            <option value="ignorado" ${c.label === "ignorado" ? "selected" : ""}>Ignorado</option>
          </select>
          <button class="secondary" onclick="relabelCluster(${c.id})">Guardar</button>
          <button class="danger" onclick="deleteCluster(${c.id})">Eliminar</button>
        </span>
      </div>`
    )
    .join("");
}

async function relabelCluster(id) {
  const value = document.getElementById(`label-${id}`).value || null;
  await api(`/api/clusters/${id}`, { method: "PATCH", body: JSON.stringify({ label: value }) });
  await loadClusters();
}

async function deleteCluster(id) {
  if (!confirm("¿Eliminar este patrón? Los segmentos volverán a 'desconocido'.")) return;
  await api(`/api/clusters/${id}`, { method: "DELETE" });
  await loadClusters();
}

// -- arranque -----------------------------------------------------------------

connectWs((msg) => {
  if (msg.type === "radio_status" && msg.data.radio_id === radioId) {
    // El worker no sabe nada del túnel de Cloudflare (lo gestiona el
    // WorkerManager aparte), así que sus mensajes no traen esos campos:
    // se fusiona sobre `radio` en vez de sustituirlo, para no perder el
    // estado del túnel en cada actualización en vivo.
    if (radio) Object.assign(radio, msg.data);
    applyStatus(radio || msg.data);
  }
});

loadRadio();
loadPendientes();
loadHistorial();
