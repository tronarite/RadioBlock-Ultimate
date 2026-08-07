from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.models import Radio
from app.db.session import get_db
from app.api.schemas import RadioCreate, RadioOut, RadioStatusOut, RadioUpdate

router = APIRouter(prefix="/api/radios", tags=["radios"])


@router.get("", response_model=list[RadioStatusOut])
def list_radios(request: Request, db: Session = Depends(get_db)):
    manager = request.app.state.manager
    radios = db.query(Radio).all()
    out = []
    for r in radios:
        status = manager.status(r.id) or {}
        out.append(
            RadioStatusOut(
                **RadioOut.model_validate(r).model_dump(),
                state=status.get("state", "caido"),
                connected=status.get("connected", False),
                pending_count=status.get("pending_count", 0),
                n_clients=status.get("n_clients", 0),
            )
        )
    return out


@router.post("", response_model=RadioOut)
def create_radio(payload: RadioCreate, db: Session = Depends(get_db)):
    radio = Radio(**payload.model_dump())
    db.add(radio)
    db.commit()
    db.refresh(radio)
    return radio


@router.get("/{radio_id}", response_model=RadioStatusOut)
def get_radio(radio_id: int, request: Request, db: Session = Depends(get_db)):
    radio = db.get(Radio, radio_id)
    if not radio:
        raise HTTPException(404, "radio not found")
    status = request.app.state.manager.status(radio_id) or {}
    return RadioStatusOut(
        **RadioOut.model_validate(radio).model_dump(),
        state=status.get("state", "caido"),
        connected=status.get("connected", False),
        pending_count=status.get("pending_count", 0),
        n_clients=status.get("n_clients", 0),
    )


@router.patch("/{radio_id}", response_model=RadioOut)
def update_radio(radio_id: int, payload: RadioUpdate, request: Request, db: Session = Depends(get_db)):
    radio = db.get(Radio, radio_id)
    if not radio:
        raise HTTPException(404, "radio not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(radio, field, value)
    db.commit()
    db.refresh(radio)
    request.app.state.manager.refresh_config(radio)
    return radio


@router.delete("/{radio_id}")
def delete_radio(radio_id: int, request: Request, db: Session = Depends(get_db)):
    radio = db.get(Radio, radio_id)
    if not radio:
        raise HTTPException(404, "radio not found")
    if radio.activa:
        request.app.state.manager.stop_radio(radio_id)
    db.delete(radio)
    db.commit()
    return {"ok": True}


@router.post("/{radio_id}/activar", response_model=RadioOut)
def activar_radio(radio_id: int, request: Request, db: Session = Depends(get_db)):
    radio = db.get(Radio, radio_id)
    if not radio:
        raise HTTPException(404, "radio not found")
    radio.activa = True
    manager = request.app.state.manager
    worker = manager.start_radio(radio)
    radio.proxy_port = worker.port
    db.commit()
    db.refresh(radio)
    return radio


@router.post("/{radio_id}/desactivar", response_model=RadioOut)
def desactivar_radio(radio_id: int, request: Request, db: Session = Depends(get_db)):
    radio = db.get(Radio, radio_id)
    if not radio:
        raise HTTPException(404, "radio not found")
    radio.activa = False
    request.app.state.manager.stop_radio(radio_id)
    radio.proxy_port = None
    db.commit()
    db.refresh(radio)
    return radio
