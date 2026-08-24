"""
Cliente mínimo de la YouTube Data API v3 (vía oficial).
Todo lo que toca YouTube pasa por aquí y suma al contador de cuota.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from fastapi import HTTPException
from sqlalchemy.orm import Session

from main import BACKEND_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
from models import OwnChannel, QuotaLog

YT_API = "https://www.googleapis.com/youtube/v3"
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"

# youtube.force-ssl cubre captions.download y videos.update (lectura + escritura del canal propio)
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
REDIRECT_URI = f"{BACKEND_URL}/own-channels/oauth/callback"

# Costo en unidades por endpoint (los que usamos)
QUOTA_COST = {
    "channels.list": 1,
    "videos.list": 1,
    "playlistItems.list": 1,
    "captions.list": 50,
    "captions.download": 200,
    "videos.update": 50,
    "search.list": 100,
}


# ───────────────────────── Cuota ─────────────────────────
def pacific_day() -> str:
    """La cuota resetea a medianoche hora del Pacífico (UTC-7/-8). Aproximamos con -7."""
    return (datetime.now(timezone.utc) - timedelta(hours=7)).strftime("%Y-%m-%d")


def log_quota(db: Session, endpoint: str, calls: int = 1) -> None:
    day = pacific_day()
    row = db.query(QuotaLog).filter(QuotaLog.day == day).first()
    if not row:
        row = QuotaLog(day=day, units_used=0, searches_used=0)
        db.add(row)
    row.units_used += QUOTA_COST.get(endpoint, 1) * calls
    if endpoint == "search.list":
        row.searches_used += calls
    db.commit()


def quota_today(db: Session) -> dict:
    row = db.query(QuotaLog).filter(QuotaLog.day == pacific_day()).first()
    return {
        "day": pacific_day(),
        "units_used": row.units_used if row else 0,
        "units_limit": 10000,
        "searches_used": row.searches_used if row else 0,
        "searches_limit": 100,
    }


# ───────────────────────── OAuth ─────────────────────────
def oauth_url(state: str) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",   # necesario para recibir refresh_token
        "prompt": "consent",        # fuerza refresh_token aunque ya se haya autorizado antes
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    r = requests.post(GOOGLE_TOKEN, data={
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }, timeout=20)
    if r.status_code != 200:
        raise HTTPException(400, f"Google rechazó el código: {r.text}")
    return r.json()


def refresh_access_token(refresh_token: str) -> dict:
    r = requests.post(GOOGLE_TOKEN, data={
        "refresh_token": refresh_token,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }, timeout=20)
    if r.status_code != 200:
        raise HTTPException(401, "No se pudo renovar el acceso a YouTube; reconecta el canal")
    return r.json()


def get_valid_token(channel: OwnChannel, db: Session) -> str:
    """Devuelve un access_token vigente, renovándolo si venció (con 2 min de margen)."""
    now = datetime.now(timezone.utc)
    if channel.access_token and channel.token_expires_at and channel.token_expires_at > now + timedelta(minutes=2):
        return channel.access_token
    data = refresh_access_token(channel.refresh_token)
    channel.access_token = data["access_token"]
    channel.token_expires_at = now + timedelta(seconds=int(data.get("expires_in", 3600)))
    db.commit()
    return channel.access_token


# ───────────────────────── Llamadas ─────────────────────────
def yt_get(db: Session, endpoint: str, params: dict, access_token: str | None = None) -> dict:
    """GET a la API. endpoint = 'videos.list' → /videos. Con token OAuth o con API key pública."""
    resource = endpoint.split(".")[0]
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
    r = requests.get(f"{YT_API}/{resource}", params=params, headers=headers, timeout=30)
    log_quota(db, endpoint)
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"YouTube {endpoint}: {r.text[:300]}")
    return r.json()


def yt_put(db: Session, endpoint: str, params: dict, body: dict, access_token: str) -> dict:
    resource = endpoint.split(".")[0]
    r = requests.put(
        f"{YT_API}/{resource}", params=params, json=body,
        headers={"Authorization": f"Bearer {access_token}"}, timeout=30,
    )
    log_quota(db, endpoint)
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"YouTube {endpoint}: {r.text[:300]}")
    return r.json()


def my_channel(db: Session, access_token: str) -> dict:
    """channels.list mine=true → id, título, miniatura, playlist de subidas."""
    data = yt_get(db, "channels.list", {"part": "snippet,contentDetails", "mine": "true"}, access_token)
    items = data.get("items") or []
    if not items:
        raise HTTPException(400, "Esta cuenta de Google no tiene canal de YouTube")
    c = items[0]
    return {
        "channel_id": c["id"],
        "title": c["snippet"]["title"],
        "thumbnail_url": c["snippet"]["thumbnails"].get("default", {}).get("url", ""),
        "uploads_playlist_id": c["contentDetails"]["relatedPlaylists"]["uploads"],
    }


# ───────────────────────── Videos propios ─────────────────────────
def uploads_playlist_id(channel_id: str) -> str:
    """Convención de YouTube: la playlist de subidas es 'UU' + el resto del id del canal."""
    return "UU" + channel_id[2:]


def parse_duration(iso: str) -> int:
    """PT1H2M3S → segundos."""
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def list_own_videos(db: Session, access_token: str, channel_id: str, max_results: int = 25) -> list[dict]:
    """Últimos videos del canal propio (incluye privados y no listados). 2 unidades."""
    pl = yt_get(db, "playlistItems.list", {
        "part": "snippet,contentDetails",
        "playlistId": uploads_playlist_id(channel_id),
        "maxResults": min(max_results, 50),
    }, access_token)
    ids = [it["contentDetails"]["videoId"] for it in pl.get("items", [])]
    if not ids:
        return []
    vids = yt_get(db, "videos.list", {
        "part": "snippet,contentDetails,status,statistics",
        "id": ",".join(ids),
    }, access_token)
    out = []
    for v in vids.get("items", []):
        sn, cd, st = v["snippet"], v["contentDetails"], v.get("status", {})
        out.append({
            "video_id": v["id"],
            "title": sn.get("title", ""),
            "description": sn.get("description", ""),
            "tags": sn.get("tags", []),
            "thumbnail_url": (sn.get("thumbnails", {}).get("medium") or sn.get("thumbnails", {}).get("default") or {}).get("url", ""),
            "published_at": sn.get("publishedAt"),
            "duration_seconds": parse_duration(cd.get("duration", "")),
            "privacy": st.get("privacyStatus", ""),
            "views": int(v.get("statistics", {}).get("viewCount", 0) or 0),
        })
    return out


def get_video_snippet(db: Session, access_token: str, video_id: str) -> dict:
    data = yt_get(db, "videos.list", {"part": "snippet", "id": video_id}, access_token)
    items = data.get("items") or []
    if not items:
        raise HTTPException(404, "Video no encontrado en YouTube")
    return items[0]["snippet"]


def update_video_metadata(db: Session, access_token: str, video_id: str, title: str, description: str, tags: list[str]) -> dict:
    """videos.update part=snippet (50 unidades). Conserva categoría e idioma actuales."""
    current = get_video_snippet(db, access_token, video_id)
    snippet = {
        "title": title[:100],
        "description": description[:5000],
        "tags": [t[:100] for t in tags][:60],
        "categoryId": current.get("categoryId", "22"),
    }
    if current.get("defaultLanguage"):
        snippet["defaultLanguage"] = current["defaultLanguage"]
    return yt_put(db, "videos.update", {"part": "snippet"}, {"id": video_id, "snippet": snippet}, access_token)


# ───────────────────────── Subtítulos del video propio ─────────────────────────
def _srt_to_segments(srt: str) -> list[dict]:
    """Convierte SRT en [{start: segundos, text}]."""
    import re
    segs = []
    for block in re.split(r"\n\s*\n", srt.strip()):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->", lines[0] if "-->" in lines[0] else (lines[1] if len(lines) > 1 else ""))
        if not m:
            continue
        start = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        text_lines = lines[2:] if "-->" in lines[1] else lines[1:]
        text = " ".join(text_lines)
        text = re.sub(r"<[^>]+>", "", text)
        if text:
            segs.append({"start": start, "text": text})
    return segs


def get_own_transcript(db: Session, access_token: str, video_id: str) -> tuple[list[dict], str]:
    """
    Devuelve (segmentos, fuente). Fuente 'oficial' = captions.download; 'no_oficial' = librería de respaldo.
    Intenta primero la vía oficial (subtítulos subidos o automáticos del canal propio).
    """
    # 1) Vía oficial
    try:
        tracks = yt_get(db, "captions.list", {"part": "snippet", "videoId": video_id}, access_token).get("items", [])
    except HTTPException:
        tracks = []
    # Preferimos subtítulos manuales; si no hay, los automáticos (asr)
    tracks.sort(key=lambda t: (t["snippet"].get("trackKind") == "asr", t["snippet"].get("language") != "es"))
    for t in tracks:
        r = requests.get(
            f"{YT_API}/captions/{t['id']}", params={"tfmt": "srt"},
            headers={"Authorization": f"Bearer {access_token}"}, timeout=60,
        )
        log_quota(db, "captions.download")
        if r.status_code == 200 and r.text.strip():
            segs = _srt_to_segments(r.text)
            if segs:
                return segs, "oficial"
    # 2) Respaldo no oficial (misma librería que usan las extensiones)
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=["es", "es-419", "es-ES", "en"])
        segs = [{"start": int(s.start), "text": s.text.replace("\n", " ")} for s in fetched]
        if segs:
            return segs, "no_oficial"
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            404,
            "No se encontraron subtítulos para este video. Si es reciente, YouTube tarda unos minutos en generar los automáticos; "
            f"si es privado, la vía de respaldo no puede leerlo. Detalle: {str(e)[:120]}",
        )
    raise HTTPException(404, "El video no tiene subtítulos disponibles")


def segments_to_text(segs: list[dict], max_chars: int = 90000) -> str:
    """Texto con marcas [mm:ss] cada ~30 s para que la IA pueda proponer capítulos."""
    out, last_mark = [], -999
    for s in segs:
        if s["start"] - last_mark >= 30:
            m, sec = divmod(s["start"], 60)
            h, m = divmod(m, 60)
            stamp = f"[{h}:{m:02d}:{sec:02d}]" if h else f"[{m:02d}:{sec:02d}]"
            out.append(f"\n{stamp} ")
            last_mark = s["start"]
        out.append(s["text"])
    text = " ".join(out)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... transcripción recortada ...]"
    return text
