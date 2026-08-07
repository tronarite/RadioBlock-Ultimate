from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Cluster, Segmento
from app.db.session import get_db
from app.api.schemas import StatsOut

router = APIRouter(tags=["stats"])


@router.get("/api/radios/{radio_id}/stats", response_model=StatsOut)
def stats(radio_id: int, db: Session = Depends(get_db)):
    total_segundos = (
        db.query(func.coalesce(func.sum(Segmento.duracion), 0.0))
        .filter(Segmento.radio_id == radio_id)
        .scalar()
    )
    muted_segundos = (
        db.query(func.coalesce(func.sum(Segmento.duracion), 0.0))
        .filter(Segmento.radio_id == radio_id, Segmento.label == "anuncio")
        .scalar()
    )
    n_patrones = (
        db.query(func.count(Cluster.id))
        .filter(Cluster.radio_id == radio_id, Cluster.label.isnot(None))
        .scalar()
    )

    rows = (
        db.query(
            func.date(Segmento.timestamp).label("dia"),
            func.coalesce(func.sum(Segmento.duracion), 0.0).label("segundos"),
        )
        .filter(Segmento.radio_id == radio_id, Segmento.label == "anuncio")
        .group_by(func.date(Segmento.timestamp))
        .order_by(func.date(Segmento.timestamp))
        .all()
    )
    evolucion = [{"dia": dia, "minutos_anuncio": round(segundos / 60, 2)} for dia, segundos in rows]

    minutos_escuchados = total_segundos / 60
    minutos_mutados = muted_segundos / 60
    porcentaje = (muted_segundos / total_segundos * 100) if total_segundos else 0.0

    return StatsOut(
        radio_id=radio_id,
        minutos_escuchados=round(minutos_escuchados, 2),
        minutos_mutados=round(minutos_mutados, 2),
        porcentaje_anuncios=round(porcentaje, 2),
        n_patrones=n_patrones,
        evolucion_diaria=evolucion,
    )
