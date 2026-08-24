from pydantic import BaseModel

from main import *
from models import Niche, OwnChannel
import youtube_api as yt

router = APIRouter(tags=["own-channels"])


# ───────────────────────── Nichos ─────────────────────────
class NicheBody(BaseModel):
    name: str
    ai_profile: str = ""


@router.get("/niches")
def list_niches(role: str = Depends(require_editor), db: Session = Depends(get_db)):
    rows = db.query(Niche).order_by(Niche.name).all()
    return [{"id": n.id, "name": n.name, "ai_profile": n.ai_profile} for n in rows]


@router.post("/niches")
def create_niche(body: NicheBody, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(Niche).filter(Niche.name == body.name).first():
        raise HTTPException(400, "Ese nicho ya existe")
    n = Niche(name=body.name, ai_profile=body.ai_profile)
    db.add(n)
    db.commit()
    return {"id": n.id, "name": n.name}


@router.put("/niches/{niche_id}")
def update_niche(niche_id: int, body: NicheBody, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    n = db.get(Niche, niche_id)
    if not n:
        raise HTTPException(404, "Nicho no encontrado")
    n.name, n.ai_profile = body.name, body.ai_profile
    db.commit()
    return {"ok": True}


# ───────────────────────── Canales propios ─────────────────────────
def _public(c: OwnChannel) -> dict:
    """Vista sin tokens: es lo único que ve el frontend (editores incluidos)."""
    return {
        "id": c.id,
        "channel_id": c.channel_id,
        "title": c.title,
        "thumbnail_url": c.thumbnail_url,
        "niche_id": c.niche_id,
        "niche": c.niche.name if c.niche else None,
        "channel_links": c.channel_links,
        "connected_at": c.connected_at,
    }


@router.get("/own-channels")
def list_own_channels(role: str = Depends(require_editor), db: Session = Depends(get_db)):
    return [_public(c) for c in db.query(OwnChannel).order_by(OwnChannel.title).all()]


class OwnChannelUpdate(BaseModel):
    niche_id: int | None = None
    channel_links: str | None = None


@router.put("/own-channels/{channel_pk}")
def update_own_channel(
    channel_pk: int, body: OwnChannelUpdate, role: str = Depends(require_admin), db: Session = Depends(get_db)
):
    c = db.get(OwnChannel, channel_pk)
    if not c:
        raise HTTPException(404, "Canal no encontrado")
    if body.niche_id is not None:
        c.niche_id = body.niche_id
    if body.channel_links is not None:
        c.channel_links = body.channel_links
    db.commit()
    return _public(c)


@router.delete("/own-channels/{channel_pk}")
def disconnect_own_channel(channel_pk: int, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(OwnChannel, channel_pk)
    if not c:
        raise HTTPException(404, "Canal no encontrado")
    db.delete(c)
    db.commit()
    return {"ok": True}


# ───────────────────────── OAuth de YouTube (solo admin) ─────────────────────────
@router.get("/own-channels/oauth/start")
def oauth_start(role: str = Depends(require_admin)):
    """Devuelve la URL de Google a la que el frontend redirige al admin."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(500, "Faltan GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET en el backend")
    # El state es un token firmado de 10 min: evita que alguien meta un callback ajeno
    state = jwt.encode(
        {"purpose": "yt_oauth", "exp": now_utc() + timedelta(minutes=10)}, SECRET_KEY, algorithm="HS256"
    )
    return {"url": yt.oauth_url(state)}


@router.get("/own-channels/oauth/callback")
def oauth_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Google redirige aquí. Guardamos tokens y volvemos al frontend."""
    if error or not code or not state:
        return RedirectResponse(f"{FRONTEND_URL}/canales?error={error or 'sin_codigo'}")
    try:
        payload = jwt.decode(state, SECRET_KEY, algorithms=["HS256"])
        assert payload.get("purpose") == "yt_oauth"
    except Exception:
        return RedirectResponse(f"{FRONTEND_URL}/canales?error=state_invalido")

    tokens = yt.exchange_code(code)
    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token")
    info = yt.my_channel(db, access_token)

    c = db.query(OwnChannel).filter(OwnChannel.channel_id == info["channel_id"]).first()
    if not c:
        if not refresh_token:
            return RedirectResponse(f"{FRONTEND_URL}/canales?error=sin_refresh_token")
        c = OwnChannel(channel_id=info["channel_id"], refresh_token=refresh_token)
        db.add(c)
    elif refresh_token:
        c.refresh_token = refresh_token  # reconexión: actualizamos

    c.title = info["title"]
    c.thumbnail_url = info["thumbnail_url"]
    c.access_token = access_token
    c.token_expires_at = now_utc() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    db.commit()

    return RedirectResponse(f"{FRONTEND_URL}/canales?connected={c.id}")


@router.get("/own-channels/{channel_pk}/test")
def test_connection(channel_pk: int, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    """Comprueba que el token sigue vivo (1 unidad de cuota)."""
    c = db.get(OwnChannel, channel_pk)
    if not c:
        raise HTTPException(404, "Canal no encontrado")
    token = yt.get_valid_token(c, db)
    info = yt.my_channel(db, token)
    return {"ok": True, "channel": info["title"], "quota": yt.quota_today(db)}


@router.get("/quota")
def quota(role: str = Depends(require_admin), db: Session = Depends(get_db)):
    return yt.quota_today(db)
