import threading

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.analysis import model as model_module
from app.config import SEGMENTS_DIR
from app.db.models import Segmento
from app.db.session import SessionLocal, get_db
from app.api.schemas import SegmentoOut, SegmentoLabel

router = APIRouter(tags=["segmentos"])


def _retrain_async(radio_id: int) -> None:
    def _run():
        db = SessionLocal()
        try:
            model_module.retrain(db, radio_id)
        finally:
            db.close()

    threading.Thread(target=_run, daemon=True).start()


@router.get("/api/radios/{radio_id}/segmentos/pendientes", response_model=list[SegmentoOut])
def pendientes(radio_id: int, db: Session = Depends(get_db)):
    return (
        db.query(Segmento)
        .filter(
            Segmento.radio_id == radio_id,
            Segmento.label == "desconocido",
            Segmento.label_usuario.is_(None),
        )
        .order_by(Segmento.timestamp.desc())
        .all()
    )


@router.get("/api/radios/{radio_id}/segmentos/historial", response_model=list[SegmentoOut])
def historial(radio_id: int, limit: int = 50, db: Session = Depends(get_db)):
    return (
        db.query(Segmento)
        .filter(Segmento.radio_id == radio_id, Segmento.label == "anuncio")
        .order_by(Segmento.timestamp.desc())
        .limit(limit)
        .all()
    )


@router.post("/api/segmentos/{segmento_id}/etiquetar", response_model=SegmentoOut)
def etiquetar(segmento_id: int, payload: SegmentoLabel, db: Session = Depends(get_db)):
    if payload.label not in ("anuncio", "musica", "ignorar"):
        raise HTTPException(400, "label debe ser 'anuncio', 'musica' o 'ignorar'")

    seg = db.get(Segmento, segmento_id)
    if not seg:
        raise HTTPException(404, "segmento not found")

    seg.label_usuario = "ignorado" if payload.label == "ignorar" else payload.label
    db.commit()
    db.refresh(seg)

    if payload.label != "ignorar":
        _retrain_async(seg.radio_id)

    return seg


@router.get("/api/segmentos/{segmento_id}/audio")
def audio(segmento_id: int, db: Session = Depends(get_db)):
    seg = db.get(Segmento, segmento_id)
    if not seg or not seg.archivo_audio:
        raise HTTPException(404, "audio not found")
    path = SEGMENTS_DIR / seg.archivo_audio
    if not path.exists():
        raise HTTPException(404, "audio file missing on disk")
    return FileResponse(str(path), media_type="audio/wav")
