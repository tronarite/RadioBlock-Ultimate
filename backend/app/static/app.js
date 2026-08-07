// Utilidades compartidas: conexión websocket con reintento y helpers de fetch.

function connectWs(onMessage) {
  const indicator = document.getElementById("ws-indicator");
  let ws;

  function open() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onopen = () => { if (indicator) indicator.textContent = "en vivo"; };
    ws.onclose = () => {
      if (indicator) indicator.textContent = "reconectando…";
      setTimeout(open, 2000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (ev) => {
      try {
        onMessage(JSON.parse(ev.data));
      } catch (e) {
        console.error("mensaje ws inválido", e);
      }
    };
  }

  open();
}

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : null;
}

function fmtLabel(state) {
  return { musica: "Música", anuncio: "Anuncio", silencio: "Silencio", caido: "Caído" }[state] || state;
}
