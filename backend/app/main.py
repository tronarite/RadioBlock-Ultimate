import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import clusters, radios, segments, stats, ws
from app.db.models import Radio
from app.db.session import SessionLocal, init_db
from app.worker.manager import WorkerManager

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Radio Ad Blocker")


def _on_worker_state_change(radio_id: int, status: dict) -> None:
    ws.manager.broadcast_threadsafe({"type": "radio_status", "data": status})


app.state.manager = WorkerManager(
    session_factory=SessionLocal, on_state_change=_on_worker_state_change
)

app.include_router(radios.router)
app.include_router(segments.router)
app.include_router(clusters.router)
app.include_router(stats.router)
app.include_router(ws.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    from fastapi.responses import FileResponse

    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/radio/{radio_id}")
def radio_detail_page(radio_id: int):
    from fastapi.responses import FileResponse

    return FileResponse(str(STATIC_DIR / "radio.html"))


@app.on_event("startup")
async def on_startup():
    init_db()
    ws.manager.bind_loop(asyncio.get_event_loop())

    # Recupera el estado tras un reinicio del backend: relanza los workers
    # de las radios que estaban marcadas como activas.
    db = SessionLocal()
    try:
        for radio in db.query(Radio).filter(Radio.activa.is_(True)).all():
            worker = app.state.manager.start_radio(radio)
            radio.proxy_port = worker.port
        db.commit()
    finally:
        db.close()


@app.on_event("shutdown")
async def on_shutdown():
    for radio_id in list(app.state.manager._workers.keys()):
        app.state.manager.stop_radio(radio_id)
