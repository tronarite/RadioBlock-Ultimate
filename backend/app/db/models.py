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
    segment_duration_seconds: Mapped[int] = mapped_column(Integer, default=10)
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

    # Vector de features serializado (np.ndarray.tobytes()).
    features: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # "anuncio" | "musica" | "desconocido"
    label: Mapped[str] = mapped_column(String, default="desconocido")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    # Etiqueta puesta manualmente por el usuario (ancla el cluster). None si no etiquetado aún.
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

    # "anuncio" | "musica" | None (sin etiquetar todavía)
    label: Mapped[str | None] = mapped_column(String, nullable=True)

    centroid: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    n_segmentos: Mapped[int] = mapped_column(Integer, default=0)

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
