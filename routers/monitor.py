"""
Funciones C, D y G — Monitor por nicho, ranking diario de virales, canales con potencial, miniaturas.
"""
from datetime import datetime, timedelta, timezone
from statistics import median

from pydantic import BaseModel
from sqlalchemy import and_, or_

from main import *
from models import ChannelSnapshot, Niche, OwnChannel, Setting, TrackedChannel, Video, VideoSnapshot
import youtube_api as yt
import ai

router = APIRouter(prefix="/monitor", tags=["monitor"])

# ───────────────────────── Umbrales por tamaño de canal (configurables desde la app) ─────────────────────────
DEFAULT_THRESHOLDS = {
    "tiers": [
        {"max_subs": 10_000,   "fire": 30_000,  "fire2": 50_000},
        {"max_subs": 50_000,   "fire": 75_000,  "fire2": 150_000},
        {"max_subs": 200_000,  "fire": 100_000, "fire2": 200_000},
    ],
    "big_channel_multiplier": 5.0,   # 200k+: viral si supera 5× su promedio (sin alerta)
    "small_channel_max_subs": 50_000, # "canales con potencial" = por debajo de esto
    "ranking_window_days": 45,        # un viral se queda en el ranking hasta esta edad
    "min_subs": 1_000,                # canales por debajo no cuentan para 🔥
}


def get_thresholds(db: Session) -> dict:
    row = db.get(Setting, "thresholds")
    return {**DEFAULT_THRESHOLDS, **(row.value if row else {})}


def fire_for(views: int, subs: int, avg_views: float, th: dict) -> int:
    if subs < th["min_subs"]:
        return 0
    for tier in th["tiers"]:
        if subs < tier["max_subs"]:
            if views >= tier["fire2"]:
                return 2
            if views >= tier["fire"]:
                return 1
            return 0
    # canal grande: solo por múltiplo del promedio
    if avg_views > 0 and views >= avg_views * th["big_channel_multiplier"]:
        return 1
    return 0


def _token(db: Session) -> str:
    """Usamos el OAuth de cualquier canal propio para leer datos públicos (misma cuota del proyecto)."""
    c = db.query(OwnChannel).first()
    if not c:
        raise HTTPException(400, "Conecta al menos un canal propio para poder leer datos de YouTube")
    return yt.get_valid_token(c, db)


# ───────────────────────── Ajustes ─────────────────────────
@router.get("/thresholds")
def read_thresholds(role: str = Depends(require_admin), db: Session = Depends(get_db)):
    return get_thresholds(db)


@router.put("/thresholds")
def write_thresholds(body: dict, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.get(Setting, "thresholds") or Setting(key="thresholds", value={})
    row.value = {**get_thresholds(db), **body}
    db.add(row)
    db.commit()
    return row.value


# ───────────────────────── Canales monitoreados ─────────────────────────
def _channel_out(c: TrackedChannel) -> dict:
    return {
        "id": c.id, "niche_id": c.niche_id, "channel_id": c.channel_id, "title": c.title, "handle": c.handle,
        "thumbnail_url": c.thumbnail_url, "subscriber_count": c.subscriber_count, "total_views": c.total_views,
        "video_count": c.video_count, "avg_views_recent": round(c.avg_views_recent), "videos_per_week": round(c.videos_per_week, 1),
        "subs_gained_7d": c.subs_gained_7d, "views_gained_7d": c.views_gained_7d, "language": c.language,
        "channel_created_at": c.channel_created_at, "active": c.active, "last_snapshot_at": c.last_snapshot_at,
        "url": f"https://www.youtube.com/channel/{c.channel_id}",
    }


class AddChannelBody(BaseModel):
    niche_id: int
    ref: str            # URL, @handle o id
    language: str = ""  # es / en


@router.get("/channels")
def list_channels(niche_id: int | None = None, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    q = db.query(TrackedChannel)
    if niche_id:
        q = q.filter(TrackedChannel.niche_id == niche_id)
    rows = q.order_by(TrackedChannel.subscriber_count.desc()).all()
    return [_channel_out(c) for c in rows]


@router.post("/channels")
def add_channel(body: AddChannelBody, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    if not db.get(Niche, body.niche_id):
        raise HTTPException(404, "Nicho no encontrado")
    token = _token(db)
    info = yt.resolve_channel(db, token, body.ref)
    c = db.query(TrackedChannel).filter(TrackedChannel.channel_id == info["channel_id"]).first()
    if c:
        raise HTTPException(400, f"“{c.title}” ya está en el monitor")
    c = TrackedChannel(
        niche_id=body.niche_id, channel_id=info["channel_id"], title=info["title"], handle=info["handle"],
        thumbnail_url=info["thumbnail_url"], uploads_playlist_id=info["uploads_playlist_id"],
        subscriber_count=info["subscriber_count"], total_views=info["total_views"], video_count=info["video_count"],
        language=body.language,
        channel_created_at=datetime.fromisoformat(info["published_at"].replace("Z", "+00:00")) if info.get("published_at") else None,
    )
    db.add(c)
    db.commit()
    # Primer snapshot inmediato: así el ranking tiene algo desde el minuto uno
    snapshot_channel(db, token, c, get_thresholds(db))
    db.commit()
    return _channel_out(c)


class ChannelPatch(BaseModel):
    niche_id: int | None = None
    language: str | None = None
    active: bool | None = None


@router.put("/channels/{pk}")
def update_channel(pk: int, body: ChannelPatch, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(TrackedChannel, pk)
    if not c:
        raise HTTPException(404, "Canal no encontrado")
    if body.niche_id is not None:
        c.niche_id = body.niche_id
    if body.language is not None:
        c.language = body.language
    if body.active is not None:
        c.active = body.active
    db.commit()
    return _channel_out(c)


@router.delete("/channels/{pk}")
def delete_channel(pk: int, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(TrackedChannel, pk)
    if not c:
        raise HTTPException(404, "Canal no encontrado")
    for v in c.videos:
        for s in v.snapshots:
            db.delete(s)
        db.delete(v)
    for s in db.query(ChannelSnapshot).filter(ChannelSnapshot.channel_id == c.id).all():
        db.delete(s)
    db.delete(c)
    db.commit()
    return {"ok": True}


# ───────────────────────── Snapshot ─────────────────────────
def snapshot_channel(db: Session, token: str, c: TrackedChannel, th: dict) -> dict:
    """Lee los últimos 50 videos del canal, guarda snapshot del día y recalcula métricas. ~3 unidades."""
    today = yt.pacific_day()
    now = datetime.now(timezone.utc)

    # Stats del canal
    stats = yt.channels_stats(db, token, [c.channel_id]).get(c.channel_id)
    if stats:
        c.subscriber_count = stats["subscriber_count"]
        c.total_views = stats["total_views"]
        c.video_count = stats["video_count"]
        c.title = stats["title"] or c.title
        c.thumbnail_url = stats["thumbnail_url"] or c.thumbnail_url
        c.handle = stats["handle"] or c.handle
        if not c.uploads_playlist_id:
            c.uploads_playlist_id = stats["uploads_playlist_id"]
    cs = db.query(ChannelSnapshot).filter(ChannelSnapshot.channel_id == c.id, ChannelSnapshot.taken_on == today).first()
    if not cs:
        db.add(ChannelSnapshot(channel_id=c.id, taken_on=today, subscriber_count=c.subscriber_count,
                               total_views=c.total_views, video_count=c.video_count))
    # Crecimiento 7 días
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    old = (db.query(ChannelSnapshot).filter(ChannelSnapshot.channel_id == c.id, ChannelSnapshot.taken_on <= week_ago)
           .order_by(ChannelSnapshot.taken_on.desc()).first())
    if old:
        c.subs_gained_7d = c.subscriber_count - old.subscriber_count
        c.views_gained_7d = c.total_views - old.total_views

    # Videos
    ids = yt.recent_uploads(db, token, c.uploads_playlist_id)
    details = yt.videos_details(db, token, ids) if ids else []
    by_id = {v.video_id: v for v in db.query(Video).filter(Video.video_id.in_(ids)).all()} if ids else {}
    new_or_updated = 0
    for d in details:
        v = by_id.get(d["video_id"])
        pub = datetime.fromisoformat(d["published_at"].replace("Z", "+00:00")) if d.get("published_at") else None
        if not v:
            v = Video(video_id=d["video_id"], channel_id=c.id, published_at=pub)
            db.add(v)
            by_id[d["video_id"]] = v
        v.title, v.description, v.tags = d["title"], d["description"], d["tags"]
        v.thumbnail_url, v.duration_seconds, v.is_short = d["thumbnail_url"], d["duration_seconds"], d["is_short"]
        prev_views = v.latest_views
        v.latest_views = d["views"]
        # Snapshot del día (uno por video y día)
        db.flush()
        snap = db.query(VideoSnapshot).filter(VideoSnapshot.video_id == v.id, VideoSnapshot.taken_on == today).first()
        if snap:
            snap.views, snap.likes, snap.comments = d["views"], d["likes"], d["comments"]
        else:
            db.add(VideoSnapshot(video_id=v.id, taken_on=today, views=d["views"], likes=d["likes"], comments=d["comments"]))
        # Velocidad: vistas ganadas por día entre el snapshot anterior y hoy
        prev = (db.query(VideoSnapshot).filter(VideoSnapshot.video_id == v.id, VideoSnapshot.taken_on < today)
                .order_by(VideoSnapshot.taken_on.desc()).first())
        if prev:
            days = max((now - prev.taken_at).total_seconds() / 86400, 0.5)
            v.velocity_per_day = max((d["views"] - prev.views) / days, 0)
        elif pub:
            age_days = max((now - pub).total_seconds() / 86400, 0.5)
            v.velocity_per_day = d["views"] / age_days if age_days <= 7 else 0.0
        # Vistas a 3 y 7 días (se fijan la primera vez que pasamos por esa edad)
        if pub:
            age = (now - pub).total_seconds() / 86400
            if v.views_at_3d is None and 3 <= age < 4.5:
                v.views_at_3d = d["views"]
            if v.views_at_7d is None and 7 <= age < 8.5:
                v.views_at_7d = d["views"]
        new_or_updated += 1

    # Promedio del canal: mediana de los videos largos de más de 3 días (excluye el efecto de un viral reciente)
    long_videos = [v for v in by_id.values() if not v.is_short and v.published_at and (now - v.published_at).days >= 3]
    views_list = sorted([v.latest_views for v in long_videos])
    if views_list:
        c.avg_views_recent = float(median(views_list))
    # Ritmo de publicación (videos por semana en los últimos 30 días)
    recent = [v for v in by_id.values() if v.published_at and (now - v.published_at).days <= 30]
    c.videos_per_week = len(recent) / (30 / 7)

    # Score outlier y fuego
    for v in by_id.values():
        v.outlier_score = (v.latest_views / c.avg_views_recent) if c.avg_views_recent > 0 else 0.0
        fl = 0 if v.is_short else fire_for(v.latest_views, c.subscriber_count, c.avg_views_recent, th)
        if fl > 0 and v.fire_level == 0:
            v.first_fire_at = now
        v.fire_level = fl

    c.last_snapshot_at = now
    return {"channel": c.title, "videos": new_or_updated}


def _fill_keywords(db: Session, videos: list[Video]) -> int:
    """Palabra clave por IA para los virales que aún no la tienen. Una llamada por lote de 20."""
    todo = [v for v in videos if not v.keyword]
    done = 0
    for i in range(0, len(todo), 20):
        batch = todo[i:i + 20]
        listing = "\n".join(f'{v.id}: TÍTULO="{v.title}" ETIQUETAS={", ".join((v.tags or [])[:12])}' for v in batch)
        try:
            res = ai.chat_json(
                "Eres analista SEO de YouTube. Para cada video, devuelve la palabra clave principal (2-5 palabras, en el idioma del título) "
                "que explica por qué la gente hace clic: el tema exacto, no una categoría genérica. Responde SOLO JSON: {\"<id>\": \"palabra clave\", ...}",
                listing, temperature=0.2, max_tokens=1200,
            )
        except HTTPException:
            break
        for v in batch:
            kw = res.get(str(v.id))
            if kw:
                v.keyword = str(kw)[:255]
                done += 1
    return done


def run_snapshot(db: Session, only_channel_id: int | None = None) -> dict:
    token = _token(db)
    th = get_thresholds(db)
    q = db.query(TrackedChannel).filter(TrackedChannel.active == True)  # noqa: E712
    if only_channel_id:
        q = q.filter(TrackedChannel.id == only_channel_id)
    results, errors = [], []
    for c in q.all():
        try:
            results.append(snapshot_channel(db, token, c, th))
            db.commit()
        except HTTPException as e:  # un canal roto no debe parar a los demás
            db.rollback()
            errors.append({"channel": c.title, "error": e.detail})
    # Palabras clave de los virales sin keyword
    fires = db.query(Video).filter(Video.fire_level > 0, Video.keyword == "").all()
    kw = _fill_keywords(db, fires)
    db.commit()
    return {"channels": len(results), "errors": errors, "keywords_added": kw, "quota": yt.quota_today(db)}


@router.post("/snapshot")
def snapshot_cron(_: None = Depends(require_cron), db: Session = Depends(get_db)):
    """Lo dispara el ping diario externo (cabecera X-Cron-Secret o ?secret=)."""
    return run_snapshot(db)


@router.get("/snapshot")
def snapshot_cron_get(_: None = Depends(require_cron), db: Session = Depends(get_db)):
    """Misma acción por GET para servicios de cron que solo hacen GET."""
    return run_snapshot(db)


@router.post("/snapshot/run")
def snapshot_manual(channel_pk: int | None = None, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    return run_snapshot(db, channel_pk)


# ───────────────────────── Ranking de virales ─────────────────────────
def _video_out(v: Video, c: TrackedChannel, now: datetime) -> dict:
    age_days = (now - v.published_at).total_seconds() / 86400 if v.published_at else None
    return {
        "id": v.id, "video_id": v.video_id, "title": v.title, "thumbnail_url": v.thumbnail_url,
        "url": f"https://www.youtube.com/watch?v={v.video_id}",
        "channel": {"id": c.id, "title": c.title, "subscriber_count": c.subscriber_count, "thumbnail_url": c.thumbnail_url,
                    "language": c.language, "avg_views_recent": round(c.avg_views_recent)},
        "views": v.latest_views, "velocity_per_day": round(v.velocity_per_day), "outlier_score": round(v.outlier_score, 1),
        "fire_level": v.fire_level, "keyword": v.keyword, "status": v.status, "is_short": v.is_short,
        "duration_seconds": v.duration_seconds, "published_at": v.published_at,
        "age_days": round(age_days, 1) if age_days is not None else None,
        "views_at_3d": v.views_at_3d, "views_at_7d": v.views_at_7d, "tags": v.tags or [],
    }


def _rank_score(v: Video, c: TrackedChannel, th: dict) -> float:
    """Combina atipicidad, fuego y velocidad. La velocidad se normaliza por el umbral del escalón del canal."""
    tier_fire = next((t["fire"] for t in th["tiers"] if c.subscriber_count < t["max_subs"]), th["tiers"][-1]["fire"])
    vel = v.velocity_per_day / max(tier_fire, 1)          # 1.0 = gana un "umbral" por día
    return v.fire_level * 2 + min(v.outlier_score, 30) * 0.3 + vel * 5


@router.get("/ranking")
def ranking(
    niche_id: int,
    limit: int = 10,
    small_only: bool = False,
    include_shorts: bool = False,
    include_done: bool = False,
    role: str = Depends(require_editor),
    db: Session = Depends(get_db),
):
    th = get_thresholds(db)
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=th["ranking_window_days"])
    q = (db.query(Video, TrackedChannel).join(TrackedChannel, Video.channel_id == TrackedChannel.id)
         .filter(TrackedChannel.niche_id == niche_id, TrackedChannel.active == True,  # noqa: E712
                 Video.published_at >= since, Video.fire_level > 0))
    if not include_shorts:
        q = q.filter(Video.is_short == False)  # noqa: E712
    if not include_done:
        q = q.filter(or_(Video.status == "", Video.status == None, Video.status.in_(["elegido", "en_produccion"])))  # noqa: E711
    if small_only:
        q = q.filter(TrackedChannel.subscriber_count < th["small_channel_max_subs"])
    rows = q.all()
    rows.sort(key=lambda rc: _rank_score(rc[0], rc[1], th), reverse=True)
    out = [{"rank": i + 1, **_video_out(v, c, now)} for i, (v, c) in enumerate(rows[:limit])]
    has_velocity = any(v.velocity_per_day > 0 for v, _ in rows)
    return {"items": out, "total": len(rows), "has_velocity": has_velocity, "thresholds": th, "quota": yt.quota_today(db)}


class StatusBody(BaseModel):
    status: str  # "", elegido, en_produccion, terminado, descartado


@router.put("/videos/{pk}/status")
def set_status(pk: int, body: StatusBody, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    v = db.get(Video, pk)
    if not v:
        raise HTTPException(404, "Video no encontrado")
    if body.status not in ("", "elegido", "en_produccion", "terminado", "descartado"):
        raise HTTPException(400, "Estado no válido")
    v.status = body.status
    db.commit()
    return {"ok": True, "status": v.status}


@router.get("/videos/{pk}")
def video_detail(pk: int, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    v = db.get(Video, pk)
    if not v:
        raise HTTPException(404, "Video no encontrado")
    now = datetime.now(timezone.utc)
    snaps = db.query(VideoSnapshot).filter(VideoSnapshot.video_id == v.id).order_by(VideoSnapshot.taken_on).all()
    return {**_video_out(v, v.channel, now), "description": v.description,
            "history": [{"day": s.taken_on, "views": s.views, "likes": s.likes, "comments": s.comments} for s in snaps]}


# ───────────────────────── Canales con potencial / que suben ─────────────────────────
@router.get("/rising")
def rising(niche_id: int | None = None, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    th = get_thresholds(db)
    now = datetime.now(timezone.utc)
    q = db.query(TrackedChannel).filter(TrackedChannel.active == True)  # noqa: E712
    if niche_id:
        q = q.filter(TrackedChannel.niche_id == niche_id)
    out = []
    for c in q.all():
        fires = [v for v in c.videos if v.fire_level > 0 and not v.is_short and v.published_at and (now - v.published_at).days <= th["ranking_window_days"]]
        if not fires and c.subs_gained_7d <= 0:
            continue
        out.append({**_channel_out(c), "fire_videos": len(fires), "best_outlier": round(max((v.outlier_score for v in fires), default=0), 1),
                    "small": c.subscriber_count < th["small_channel_max_subs"],
                    "channel_age_days": (now - c.channel_created_at).days if c.channel_created_at else None})
    out.sort(key=lambda x: (x["small"], x["fire_videos"], x["subs_gained_7d"]), reverse=True)
    return out


# ───────────────────────── Miniaturas ─────────────────────────
@router.get("/thumbnails")
def thumbnails(niche_id: int, limit: int = 40, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    th = get_thresholds(db)
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=th["ranking_window_days"])
    rows = (db.query(Video, TrackedChannel).join(TrackedChannel, Video.channel_id == TrackedChannel.id)
            .filter(TrackedChannel.niche_id == niche_id, Video.published_at >= since, Video.is_short == False)  # noqa: E712
            .order_by(Video.fire_level.desc(), Video.outlier_score.desc()).limit(limit).all())
    return [_video_out(v, c, now) for v, c in rows]
