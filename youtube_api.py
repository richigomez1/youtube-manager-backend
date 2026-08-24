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
