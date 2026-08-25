"""
Esquema de la base de datos (separada de Zentrix).
Se define completo desde el inicio para que la Fase 2 (monitor) acumule data sin migraciones.
"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ───────────────────────── Nichos ─────────────────────────
class Niche(Base):
    """Nicho de contenido: psicología oscura, dinero, meditación, horóscopo..."""
    __tablename__ = "niches"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    # Perfil que calibra la IA al generar metadata para este nicho (tono, público, estilo de título)
    ai_profile: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    own_channels: Mapped[list["OwnChannel"]] = relationship(back_populates="niche")
    tracked_channels: Mapped[list["TrackedChannel"]] = relationship(back_populates="niche")


# ───────────────────────── Canales propios (OAuth) ─────────────────────────
class OwnChannel(Base):
    """Canal de Richi conectado por OAuth. Los tokens nunca salen del backend."""
    __tablename__ = "own_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(64), unique=True)  # UC...
    title: Mapped[str] = mapped_column(String(255))
    thumbnail_url: Mapped[str] = mapped_column(String(500), default="")
    niche_id: Mapped[int | None] = mapped_column(ForeignKey("niches.id"), nullable=True)

    # Datos fijos del canal que la IA inserta en descripciones (redes propias, CTA, etc.)
    channel_links: Mapped[str] = mapped_column(Text, default="")

    refresh_token: Mapped[str] = mapped_column(Text)
    access_token: Mapped[str] = mapped_column(Text, default="")
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    niche: Mapped["Niche | None"] = relationship(back_populates="own_channels")
    templates: Mapped[list["RotatingTemplate"]] = relationship(back_populates="own_channel")


# ───────────────────────── Descripciones rotativas (función B) ─────────────────────────
class RotatingTemplate(Base):
    """Plantilla por canal con variables {fecha}, {mes}, {signo}... y etiquetas fijas."""
    __tablename__ = "rotating_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    own_channel_id: Mapped[int | None] = mapped_column(ForeignKey("own_channels.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    video_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # (opcional) video fijo que se actualiza
    language: Mapped[str] = mapped_column(String(5), default="en")
    date_offset_days: Mapped[int] = mapped_column(Integer, default=1)       # 1 = la fecha de mañana
    title_template: Mapped[str] = mapped_column(Text, default="")
    description_template: Mapped[str] = mapped_column(Text, default="")
    tags_template: Mapped[str] = mapped_column(Text, default="")             # etiquetas separadas por coma, con variables
    fixed_tags: Mapped[list] = mapped_column(JSON, default=list)
    auto_daily: Mapped[bool] = mapped_column(Boolean, default=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    own_channel: Mapped["OwnChannel | None"] = relationship(back_populates="templates")


# ───────────────────────── Historial de metadata generada (función A + extras) ─────────────────────────
class MetadataHistory(Base):
    """Cada generación/escritura de metadata en un video propio. Sirve para el historial títulos vs resultados."""
    __tablename__ = "metadata_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    own_channel_id: Mapped[int] = mapped_column(ForeignKey("own_channels.id"))
    video_id: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)  # True si se escribió con videos.update
    created_by_role: Mapped[str] = mapped_column(String(20), default="editor")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ───────────────────────── Monitor de canales por nicho (función C) ─────────────────────────
class TrackedChannel(Base):
    """Canal de la competencia que se monitorea."""
    __tablename__ = "tracked_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    niche_id: Mapped[int] = mapped_column(ForeignKey("niches.id"))
    channel_id: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    thumbnail_url: Mapped[str] = mapped_column(String(500), default="")
    uploads_playlist_id: Mapped[str] = mapped_column(String(64), default="")  # UU... (barato de leer)
    subscriber_count: Mapped[int] = mapped_column(Integer, default=0)
    # Promedio de vistas de sus videos recientes → denominador del score outlier
    avg_views_recent: Mapped[float] = mapped_column(Float, default=0.0)
    videos_per_week: Mapped[float] = mapped_column(Float, default=0.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    channel_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_views: Mapped[int] = mapped_column(Integer, default=0)
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    language: Mapped[str] = mapped_column(String(5), default="")         # es / en
    subs_gained_7d: Mapped[int] = mapped_column(Integer, default=0)
    views_gained_7d: Mapped[int] = mapped_column(Integer, default=0)
    handle: Mapped[str] = mapped_column(String(100), default="")

    niche: Mapped["Niche"] = relationship(back_populates="tracked_channels")
    videos: Mapped[list["Video"]] = relationship(back_populates="channel")


class Video(Base):
    """Video de un canal monitoreado. Metadata pública que no cambia mucho."""
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str] = mapped_column(String(32), unique=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("tracked_channels.id"))
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    thumbnail_url: Mapped[str] = mapped_column(String(500), default="")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Cache de las últimas métricas calculadas (se recalculan en cada snapshot)
    latest_views: Mapped[int] = mapped_column(Integer, default=0)
    velocity_per_day: Mapped[float] = mapped_column(Float, default=0.0)   # vistas ganadas / día (último tramo)
    outlier_score: Mapped[float] = mapped_column(Float, default=0.0)      # vistas ÷ promedio del canal
    is_short: Mapped[bool] = mapped_column(Boolean, default=False)
    fire_level: Mapped[int] = mapped_column(Integer, default=0)           # 0, 1 (🔥), 2 (🔥🔥)
    keyword: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default="")           # "", elegido, en_produccion, terminado, descartado
    views_at_3d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    views_at_7d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channel: Mapped["TrackedChannel"] = relationship(back_populates="videos")
    snapshots: Mapped[list["VideoSnapshot"]] = relationship(back_populates="video")


class VideoSnapshot(Base):
    """Una lectura diaria de vistas. Con 2+ snapshots se mide velocidad."""
    __tablename__ = "video_snapshots"
    __table_args__ = (UniqueConstraint("video_id", "taken_on", name="uq_snapshot_video_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    taken_on: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD (un snapshot por día)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)

    video: Mapped["Video"] = relationship(back_populates="snapshots")


# ───────────────────────── Checklist de temas (función D) ─────────────────────────
class Topic(Base):
    """Tema a producir: por_hacer → en_produccion (Zentrix) → publicado."""
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    niche_id: Mapped[int] = mapped_column(ForeignKey("niches.id"))
    source_video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    keyword: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="por_hacer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ───────────────────────── Análisis profundo guardado (función E) ─────────────────────────
class VideoAnalysis(Base):
    """Resultado del análisis IA de un viral: palabra clave, guion, variaciones, descripción regenerada."""
    __tablename__ = "video_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    keyword: Mapped[str] = mapped_column(String(255), default="")
    keyword_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    transcript: Mapped[str] = mapped_column(Text, default="")
    title_variations: Mapped[list] = mapped_column(JSON, default=list)
    regenerated_description: Mapped[str] = mapped_column(Text, default="")
    structure_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ───────────────────────── Uso de cuota (control interno) ─────────────────────────
class QuotaLog(Base):
    """Registro diario de unidades consumidas y búsquedas hechas, para no pasarse de 10,000 / 100."""
    __tablename__ = "quota_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(10), unique=True)  # YYYY-MM-DD hora Pacífico
    units_used: Mapped[int] = mapped_column(Integer, default=0)
    searches_used: Mapped[int] = mapped_column(Integer, default=0)


# ───────────────────────── Snapshot diario de canal (para ver quién sube) ─────────────────────────
class ChannelSnapshot(Base):
    __tablename__ = "channel_snapshots"
    __table_args__ = (UniqueConstraint("channel_id", "taken_on", name="uq_channel_snapshot_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("tracked_channels.id"))
    taken_on: Mapped[str] = mapped_column(String(10))
    subscriber_count: Mapped[int] = mapped_column(Integer, default=0)
    total_views: Mapped[int] = mapped_column(Integer, default=0)
    video_count: Mapped[int] = mapped_column(Integer, default=0)


# ───────────────────────── Ajustes (umbrales de viral, etc.) ─────────────────────────
class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
