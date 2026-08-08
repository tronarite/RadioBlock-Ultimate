from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.models import Radio
from app.db.session import get_db
from app.api.schemas import MarcarActual, RadioCreate, RadioOut, RadioStatusOut, RadioUpdate

router = APIRouter(prefix="/api/radios", tags=["radios"])


def _status_out(radio: Radio, manager) -> RadioStatusOut:
    status = manager.status(radio.id) or {}
    tunnel = manager.tunnel_status(radio.id) or {}
    return RadioStatusOut(
        **RadioOut.model_validate(radio).model_dump(),
        state=status.get("state", "caido"),
        connected=status.get("connected", False),
        pending_count=status.get("pending_count", 0),
        n_clients=status.get("n_clients", 0),
        public_url=tunnel.get("url"),
        tunnel_state=tunnel.get("state", "apagado"),
    )


@router.get("", response_model=list[RadioStatusOut])
def list_radios(request: Request, db: Session = Depends(get_db)):
    manager = request.app.state.manager
    radios = db.query(Radio).all()
    return [_status_out(r, manager) for r in radios]


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
    return _status_out(radio, request.app.state.manager)


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


@router.post("/{radio_id}/marcar_actual")
def marcar_actual(radio_id: int, payload: MarcarActual, request: Request, db: Session = Depends(get_db)):
    """Marcado manual en directo: el usuario está escuchando el proxy en el
    panel y confirma en el momento que lo que suena ahora es (o no es) un
    anuncio. Cubre el margen de latencia del proxy/reproductor marcando
    también el segmento inmediatamente anterior, y arma el siguiente que se
    capture. Esta confirmación explícita del usuario es precisamente lo que
    permite silenciarlo en el futuro sin volver a preguntar."""
    radio = db.get(Radio, radio_id)
    if not radio:
        raise HTTPException(404, "radio not found")
    if not radio.activa:
        raise HTTPException(409, "la radio no está activa")

    marcados = request.app.state.manager.mark_recent(radio_id, payload.label)
    if marcados is None:
        raise HTTPException(409, "el worker de esta radio no está corriendo todavía")
    return {"marcados": marcados}


@router.post("/{radio_id}/marcar_inicio")
def marcar_inicio(radio_id: int, payload: MarcarActual, request: Request, db: Session = Depends(get_db)):
    """Empieza un marcado por tramo: los anuncios duran lo que duren (unos
    segundos o varios minutos), así que en vez de una ventana fija, todo lo
    que suene desde ahora se etiqueta hasta que se llame a /marcar_fin."""
    radio = db.get(Radio, radio_id)
    if not radio:
        raise HTTPException(404, "radio not found")
    if not radio.activa:
        raise HTTPException(409, "la radio no está activa")

    marcados = request.app.state.manager.start_marking(radio_id, payload.label)
    if marcados is None:
        raise HTTPException(409, "el worker de esta radio no está corriendo todavía")
    return {"marcados": marcados}


@router.post("/{radio_id}/marcar_fin")
def marcar_fin(radio_id: int, request: Request, db: Session = Depends(get_db)):
    """Termina el tramo abierto con /marcar_inicio."""
    radio = db.get(Radio, radio_id)
    if not radio:
        raise HTTPException(404, "radio not found")

    ok = request.app.state.manager.stop_marking(radio_id)
    if not ok:
        raise HTTPException(409, "el worker de esta radio no está corriendo todavía")
    return {"ok": True}


@router.post("/{radio_id}/tunnel/activar", response_model=RadioStatusOut)
def activar_tunnel(radio_id: int, request: Request, db: Session = Depends(get_db)):
    """Expone el proxy de esta radio a internet mediante un Cloudflare
    Quick Tunnel: una URL pública https://xxxx.trycloudflare.com que
    redirige al proxy local. No requiere cuenta ni dominio de Cloudflare,
    pero la URL es efímera (cambia cada vez que se activa) y sin garantía
    de uptime — para eso hace falta un túnel con nombre y cuenta propia."""
    radio = db.get(Radio, radio_id)
    if not radio:
        raise HTTPException(404, "radio not found")
    if not radio.activa:
        raise HTTPException(409, "activa la radio antes de exponerla a internet")

    manager = request.app.state.manager
    tunnel = manager.start_tunnel(radio_id)
    if tunnel is None:
        raise HTTPException(409, "el worker de esta radio no está corriendo todavía")
    if not tunnel.wait_ready(timeout=25) or tunnel.state != "activo":
        manager.stop_tunnel(radio_id)
        raise HTTPException(502, "no se pudo crear el túnel de Cloudflare (¿está instalado cloudflared?)")
    return _status_out(radio, manager)


@router.post("/{radio_id}/tunnel/desactivar", response_model=RadioStatusOut)
def desactivar_tunnel(radio_id: int, request: Request, db: Session = Depends(get_db)):
    radio = db.get(Radio, radio_id)
    if not radio:
        raise HTTPException(404, "radio not found")
    manager = request.app.state.manager
    manager.stop_tunnel(radio_id)
    return _status_out(radio, manager)
