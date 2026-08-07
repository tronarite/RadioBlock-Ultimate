import datetime

from sqlalchemy import Float, ForeignKey, Integer, LargeBinary, String, Boolean, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Radio(Base):
    __tablename__ = "radios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String, nullable=True)
    activa: Mapped[bool] = mapped_column(Boolean, default=False)

    # Configuración por emisora (spec: "umbral de confianza configurable por emisora").
    segment_duration_seconds: Mapped[int] = mapped_column(Integer, default=20)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.75)

    # Puerto local asignado al proxy de audio mientras la radio está activa.
    proxy_port: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    segmentos: Mapped[list["Segmento"]] = relationship(
        back_populates="radio", cascade="all, delete-orphan"
    )
    clusters: Mapped[list["Cluster"]] = relationship(
        back_populates="radio", cascade="all, delete-orphan"
    )
    muteos: Mapped[list["Muteo"]] = relationship(
        back_populates="radio", cascade="all, delete-orphan"
    )


class Segmento(Base):
    __tablename__ = "segmentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    radio_id: Mapped[int] = mapped_column(ForeignKey("radios.id"), nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    duracion: Mapped[float] = mapped_column(Float, nullable=False)

    # Huella acústica (conjunto de hashes tipo Shazam, serializado) — ver
    # `analysis/fingerprint.py`. Identifica el audio exacto de este
    # segmento, no solo su timbre general.
    fingerprint: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # "anuncio" (se silencia) | "desconocido" (no coincide con ningún patrón
    # revisado, pasa sin silenciar) | cualquier otra etiqueta que el usuario le
    # haya puesto al patrón/cluster al que pertenece este segmento (p.ej.
    # "contenido", "ignorado") — ninguna de ellas se silencia.
    label: Mapped[str] = mapped_column(String, default="desconocido")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    # Veredicto del usuario sobre el CLUSTER al que pertenece este segmento
    # (se copia a todos sus miembros al revisar un patrón). None si el patrón
    # todavía no se ha revisado.
    label_usuario: Mapped[str | None] = mapped_column(String, nullable=True)

    archivo_audio: Mapped[str | None] = mapped_column(String, nullable=True)

    cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("clusters.id"), nullable=True
    )

    radio: Mapped["Radio"] = relationship(back_populates="segmentos")
    cluster: Mapped["Cluster | None"] = relationship(back_populates="segmentos")


class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    radio_id: Mapped[int] = mapped_column(ForeignKey("radios.id"), nullable=False)

    # "anuncio" | "contenido" | "ignorado" | None (patrón detectado, aún sin revisar)
    label: Mapped[str | None] = mapped_column(String, nullable=True)

    # Huella acústica agregada (unión de los hashes de todos sus miembros) —
    # permite reconocer nuevos segmentos que sean el mismo audio, y volver a
    # identificar este patrón en el siguiente reentrenamiento aunque cambien
    # ligeramente sus miembros exactos (ver `analysis/model.retrain`).
    fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    n_segmentos: Mapped[int] = mapped_column(Integer, default=0)

    # Nº de apariciones distintas y separadas en el tiempo (no segmentos
    # consecutivos de un mismo tramo continuo). Es lo que de verdad indica
    # repetición real — ver `analysis/model._count_apariciones`.
    n_apariciones: Mapped[int] = mapped_column(Integer, default=0)

    representative_segment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    radio: Mapped["Radio"] = relationship(back_populates="clusters")
    segmentos: Mapped[list["Segmento"]] = relationship(back_populates="cluster")


class Muteo(Base):
    __tablename__ = "muteos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    radio_id: Mapped[int] = mapped_column(ForeignKey("radios.id"), nullable=False)
    timestamp_inicio: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    timestamp_fin: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    duracion: Mapped[float] = mapped_column(Float, default=0.0)

    radio: Mapped["Radio"] = relationship(back_populates="muteos")
