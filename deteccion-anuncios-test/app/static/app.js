async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

function fmtFecha(iso) {
  return new Date(iso + "Z").toLocaleString();
}

function fmtTime(s) {
  if (!isFinite(s) || s < 0) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

// Solo un audio sonando a la vez: al reproducir uno, se pausan todos los
// demás. Captura en fase de captura para que funcione también con los
// <audio> que se insertan dinámicamente (patrones, apariciones, etc).
document.addEventListener(
  "play",
  (ev) => {
    if (ev.target.tagName !== "AUDIO") return;
    document.querySelectorAll("audio").forEach((a) => {
      if (a !== ev.target && !a.paused) a.pause();
    });
  },
  true
);

// -- reproductor propio -----------------------------------------------------------------
//
// El <audio controls> nativo daba problemas (se veía "cortado", barra de
// progreso apenas manejable). Este es uno propio: SIEMPRE reproduce el
// fragmento completo de principio a fin — nunca se trunca solo — y si hay
// un tramo estimado como el anuncio exacto, se marca en rojo sobre la
// barra (haciendo clic ahí se salta a ese punto, pero la reproducción
// sigue hasta el final igualmente).

let playerSeq = 0;

function renderPlayer(src, estStart, estEnd) {
  const id = `player-${playerSeq++}`;
  return `
    <div class="player" id="${id}" data-est-start="${estStart ?? ""}" data-est-end="${estEnd ?? ""}">
      <button class="play-btn" type="button" title="Reproducir/pausar">▶</button>
      <div class="seek-wrap">
        <input type="range" class="seek" min="0" max="1000" value="0" step="1">
        <div class="seek-marker" style="display:none;" title="Tramo exacto estimado del anuncio"></div>
      </div>
      <span class="time"><span class="time-cur">0:00</span> / <span class="time-total">0:00</span></span>
      <audio preload="none" src="${src}"></audio>
    </div>`;
}

function initPlayers(root) {
  root.querySelectorAll(".player").forEach((el) => {
    if (el.dataset.wired) return;
    el.dataset.wired = "1";

    const audio = el.querySelector("audio");
    const btn = el.querySelector(".play-btn");
    const seek = el.querySelector(".seek");
    const marker = el.querySelector(".seek-marker");
    const curEl = el.querySelector(".time-cur");
    const totEl = el.querySelector(".time-total");
    const estStart = parseFloat(el.dataset.estStart);
    const estEnd = parseFloat(el.dataset.estEnd);

    btn.addEventListener("click", () => {
      if (audio.paused) audio.play();
      else audio.pause();
    });
    audio.addEventListener("play", () => (btn.textContent = "⏸"));
    audio.addEventListener("pause", () => (btn.textContent = "▶"));
    audio.addEventListener("loadedmetadata", () => {
      totEl.textContent = fmtTime(audio.duration);
      if (!isNaN(estStart) && !isNaN(estEnd) && audio.duration > 0) {
        marker.style.display = "";
        marker.style.left = `${(estStart / audio.duration) * 100}%`;
        marker.style.width = `${Math.max(((estEnd - estStart) / audio.duration) * 100, 0.5)}%`;
      }
    });
    audio.addEventListener("timeupdate", () => {
      if (audio.duration > 0) seek.value = String((audio.currentTime / audio.duration) * 1000);
      curEl.textContent = fmtTime(audio.currentTime);
    });
    seek.addEventListener("input", () => {
      if (audio.duration > 0) audio.currentTime = (Number(seek.value) / 1000) * audio.duration;
    });
    marker.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (!isNaN(estStart)) audio.currentTime = estStart;
      audio.play();
    });
  });
}

function radioCardShell(r) {
  return `
    <div class="card" id="radio-${r.key}">
      <div class="radio-head">
        <h2>${r.nombre}</h2>
        <span class="stat-mini" id="estado-${r.key}"></span>
      </div>
      <p class="muted" style="margin-top:0;">
        Fragmentos sospechosos de ser anuncio: sonaron 3+ veces en momentos distintos con la misma huella acústica.
      </p>
      <div id="grupos-${r.key}"><p class="empty">Cargando…</p></div>
    </div>`;
}

const ultimoGruposJson = {};

function renderGrupos(radioKey, grupos) {
  // El panel se refresca cada 15s; si no ha cambiado nada, no se vuelve
  // a pintar — si no, cualquier reproductor sonando en ese momento se
  // reiniciaría solo de golpe.
  const json = JSON.stringify(grupos);
  if (ultimoGruposJson[radioKey] === json) return;
  ultimoGruposJson[radioKey] = json;

  const el = document.getElementById(`grupos-${radioKey}`);
  if (grupos.length === 0) {
    el.innerHTML = `<p class="empty">Todavía no se ha detectado ningún fragmento repetido 3+ veces.</p>`;
    return;
  }
  el.innerHTML = grupos.map((g) => renderGrupo(radioKey, g)).join("");
  initPlayers(el);
}

function renderGrupo(radioKey, g) {
  const tieneEstimacion = g.inicio_estimado != null && g.fin_estimado != null;
  const src = g.representative_segment_id ? `/api/segmentos/${g.representative_segment_id}/audio` : null;

  return `
      <div class="grupo">
        <div class="grupo-head">
          <span><span class="badge-anuncio">visto ${g.n_apariciones}×</span> ${g.n_segmentos} ventana(s) en total</span>
          <button onclick="toggleApariciones('${radioKey}', ${g.id})">ver todas las apariciones</button>
        </div>
        <p class="muted" style="margin:0.4rem 0 0;">primera vez: ${fmtFecha(g.primera_vez)} — última vez: ${fmtFecha(g.ultima_vez)}</p>
        ${tieneEstimacion
          ? `<p class="muted" style="margin:0.2rem 0 0;">tramo exacto estimado (marcado en rojo en la barra): <strong>${g.inicio_estimado.toFixed(1)}s – ${g.fin_estimado.toFixed(1)}s</strong> · ${g.n_estimaciones} comparaciones (mejora con cada aparición nueva)</p>`
          : g.representative_segment_id
            ? '<p class="muted" style="margin:0.2rem 0 0;">todavía sin tramo exacto estimado (hace falta más de una comparación fiable)</p>'
            : ""}
        ${src ? renderPlayer(src, tieneEstimacion ? g.inicio_estimado : null, tieneEstimacion ? g.fin_estimado : null) : '<p class="muted">sin muestra guardada</p>'}
        <div class="apariciones-list" id="apariciones-${radioKey}-${g.id}"></div>
      </div>`;
}

async function toggleApariciones(radioKey, grupoId) {
  const el = document.getElementById(`apariciones-${radioKey}-${grupoId}`);
  if (el.classList.contains("open")) {
    el.classList.remove("open");
    return;
  }
  if (!el.dataset.loaded) {
    const segs = await api(`/api/radios/${radioKey}/grupos/${grupoId}/apariciones`);
    el.innerHTML = segs
      .map(
        (s) => `<div class="row">
          <span class="muted">${fmtFecha(s.timestamp)}</span>
          ${s.archivo_audio ? renderPlayer(`/api/segmentos/${s.id}/audio`, null, null) : '<span class="muted">audio rotado</span>'}
        </div>`
      )
      .join("");
    el.dataset.loaded = "1";
    initPlayers(el);
  }
  el.classList.add("open");
}

let inicializado = false;

async function refresh() {
  try {
    const radios = await api("/api/radios");

    if (!inicializado) {
      document.getElementById("radios-container").innerHTML = radios.map(radioCardShell).join("");
      inicializado = true;
    }

    let algunoConectado = false;
    for (const r of radios) {
      if (r.connected) algunoConectado = true;
      const estadoEl = document.getElementById(`estado-${r.key}`);
      estadoEl.innerHTML = `
        <span class="badge ${r.connected ? "ok" : "bad"}">${r.connected ? "conectada" : "reconectando…"}</span>
        ${r.n_segmentos} ventanas analizadas
      `;
    }
    document.getElementById("estado-global").textContent = algunoConectado ? "en vivo" : "sin conexión";

    await Promise.all(
      radios.map(async (r) => {
        const grupos = await api(`/api/radios/${r.key}/grupos`);
        renderGrupos(r.key, grupos);
      })
    );
  } catch (e) {
    document.getElementById("estado-global").textContent = "sin conexión con el servidor";
  }
}

refresh();
setInterval(refresh, 15000);
