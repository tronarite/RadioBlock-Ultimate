from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.analysis import model as model_module
from app.db.models import Cluster, Segmento
from app.db.session import get_db
from app.api.schemas import ClusterOut, ClusterRelabel

router = APIRouter(tags=["clusters"])


@router.get("/api/radios/{radio_id}/clusters", response_model=list[ClusterOut])
def list_clusters(radio_id: int, db: Session = Depends(get_db)):
    return db.query(Cluster).filter(Cluster.radio_id == radio_id).all()


@router.patch("/api/clusters/{cluster_id}", response_model=ClusterOut)
def relabel_cluster(cluster_id: int, payload: ClusterRelabel, db: Session = Depends(get_db)):
    cluster = db.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(404, "cluster not found")

    cluster.label = payload.label
    resolved = payload.label or "desconocido"
    for seg in db.query(Segmento).filter(Segmento.cluster_id == cluster_id).all():
        seg.label = resolved
        seg.label_usuario = payload.label

    db.commit()
    db.refresh(cluster)
    model_module.update_cluster_label_cache(cluster.radio_id, cluster_id, payload.label)
    return cluster


@router.delete("/api/clusters/{cluster_id}")
def delete_cluster(cluster_id: int, db: Session = Depends(get_db)):
    cluster = db.get(Cluster, cluster_id)
    if not cluster:
        raise HTTPException(404, "cluster not found")

    for seg in db.query(Segmento).filter(Segmento.cluster_id == cluster_id).all():
        seg.cluster_id = None
        seg.label = "desconocido"

    db.delete(cluster)
    db.commit()
    return {"ok": True}
