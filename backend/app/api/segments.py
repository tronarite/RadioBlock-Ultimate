from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import SEGMENTS_DIR
from app.db.models import Segmento
from app.db.session import get_db
from app.api.schemas import SegmentoOut

router = APIRouter(tags=["segmentos"])


@router.get("/api/radios/{radio_id}/segmentos/historial", response_model=list[SegmentoOut])
def historial(radio_id: int, limit: int = 50, db: Session = Depends(get_db)):
    return (
        db.query(Segmento)
        .filter(Segmento.radio_id == radio_id, Segmento.label == "anuncio")
        .order_by(Segmento.timestamp.desc())
        .limit(limit)
        .all()
    )


@router.get("/api/segmentos/{segmento_id}/audio")
def audio(segmento_id: int, db: Session = Depends(get_db)):
    seg = db.get(Segmento, segmento_id)
    if not seg or not seg.archivo_audio:
        raise HTTPException(404, "audio not found")
    path = SEGMENTS_DIR / seg.archivo_audio
    if not path.exists():
        raise HTTPException(404, "audio file missing on disk")
    return FileResponse(str(path), media_type="audio/wav")
