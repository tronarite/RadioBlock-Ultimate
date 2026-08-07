import datetime

from pydantic import BaseModel, ConfigDict


class RadioCreate(BaseModel):
    nombre: str
    url: str
    descripcion: str | None = None
    segment_duration_seconds: int = 10
    confidence_threshold: float = 0.75


class RadioUpdate(BaseModel):
    nombre: str | None = None
    url: str | None = None
    descripcion: str | None = None
    segment_duration_seconds: int | None = None
    confidence_threshold: float | None = None


class RadioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    url: str
    descripcion: str | None
    activa: bool
    segment_duration_seconds: int
    confidence_threshold: float
    proxy_port: int | None


class RadioStatusOut(RadioOut):
    state: str = "caido"
    connected: bool = False
    pending_count: int = 0
    n_clients: int = 0


class SegmentoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    radio_id: int
    timestamp: datetime.datetime
    duracion: float
    label: str
    confidence: float
    label_usuario: str | None
    archivo_audio: str | None


class ClusterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    radio_id: int
    label: str | None
    n_segmentos: int
    representative_segment_id: int | None


class ClusterRelabel(BaseModel):
    label: str | None


class MuteoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    radio_id: int
    timestamp_inicio: datetime.datetime
    timestamp_fin: datetime.datetime | None
    duracion: float


class StatsOut(BaseModel):
    radio_id: int
    minutos_escuchados: float
    minutos_mutados: float
    porcentaje_anuncios: float
    n_patrones: int
    evolucion_diaria: list[dict]
