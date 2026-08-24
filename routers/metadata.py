"""
Función A — Metadata de un clic.
Editor y admin pueden usarla. La cuenta de YouTube nunca sale del backend.
"""
from pydantic import BaseModel

from main import *
from models import MetadataHistory, OwnChannel
import youtube_api as yt
import ai

router = APIRouter(prefix="/metadata", tags=["metadata"])

# Costos aproximados para que el frontend los muestre
COST_GENERATE = 1 + 1 + 50 + 200   # playlist/video + captions.list + captions.download
COST_APPLY = 1 + 50                # videos.list + videos.update


def _channel(db: Session, own_channel_id: int) -> OwnChannel:
    c = db.get(OwnChannel, own_channel_id)
    if not c:
        raise HTTPException(404, "Canal no encontrado")
    return c


@router.get("/videos/{own_channel_id}")
def list_videos(own_channel_id: int, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    """Últimos videos del canal propio, para elegir cuál procesar."""
    c = _channel(db, own_channel_id)
    token = yt.get_valid_token(c, db)
    videos = yt.list_own_videos(db, token, c.channel_id)
    # Marcamos los que ya tienen metadata aplicada por la app
    applied = {
        h.video_id for h in db.query(MetadataHistory)
        .filter(MetadataHistory.own_channel_id == c.id, MetadataHistory.applied == True).all()  # noqa: E712
    }
    for v in videos:
        v["applied_by_app"] = v["video_id"] in applied
    return {"videos": videos, "quota": yt.quota_today(db)}


class GenerateBody(BaseModel):
    own_channel_id: int
    video_id: str
    notes: str = ""          # indicaciones extra del usuario (opcional)


SYSTEM_PROMPT = """Eres un especialista en SEO de YouTube para canales en español. Recibes la transcripción de un video
(con marcas de tiempo) y el perfil del canal. Devuelves SOLO un JSON con esta forma exacta:
{
  "title": "título principal (máx. 70 caracteres, sin comillas, con la palabra clave al inicio)",
  "title_alternatives": ["alt 1", "alt 2", "alt 3"],
  "keyword": "palabra clave principal del video (2-5 palabras)",
  "description": "descripción completa",
  "tags": ["etiqueta1", "etiqueta2", "..."],
  "hashtags": ["#uno", "#dos", "#tres"]
}
Reglas de la descripción:
- Primeras 2 líneas: gancho + palabra clave (es lo que se ve antes de 'Mostrar más').
- Luego un párrafo de 3-5 líneas resumiendo el valor del video de forma natural (sin listar todo).
- Luego una sección "Capítulos:" con timestamps reales tomados de las marcas de la transcripción, formato "00:00 Título del capítulo",
  entre 5 y 12 capítulos, el primero siempre 00:00, en orden y con títulos descriptivos cortos.
- Luego los enlaces/redes propios del canal EXACTAMENTE como se te dan (si se dan). Nunca inventes enlaces.
- Cierra con los 3-5 hashtags en una sola línea.
- Máximo 4500 caracteres. Sin markdown, sin asteriscos.
Etiquetas: 15-25, mezcla de palabra clave exacta, variaciones, tema amplio y nicho; en minúsculas; sin '#'.
Todo en español neutro salvo que el perfil del canal indique otra cosa. No uses clickbait vacío ni mayúsculas gritadas."""


@router.post("/generate")
def generate(body: GenerateBody, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    """Lee los subtítulos del video propio y genera título, descripción con capítulos, etiquetas y hashtags."""
    c = _channel(db, body.own_channel_id)
    token = yt.get_valid_token(c, db)

    snippet = yt.get_video_snippet(db, token, body.video_id)
    segs, source = yt.get_own_transcript(db, token, body.video_id)
    transcript = yt.segments_to_text(segs)

    niche_profile = c.niche.ai_profile if c.niche and c.niche.ai_profile else "(sin perfil definido)"
    user = f"""CANAL: {c.title}
NICHO: {c.niche.name if c.niche else "(sin nicho)"}
PERFIL DEL CANAL (cómo debe sonar): {niche_profile}
ENLACES/REDES PROPIOS (copiar tal cual en la descripción):
{c.channel_links or "(ninguno)"}

TÍTULO ACTUAL DEL VIDEO: {snippet.get("title", "")}
INDICACIONES EXTRA DEL USUARIO: {body.notes or "(ninguna)"}

TRANSCRIPCIÓN CON MARCAS DE TIEMPO:
{transcript}"""

    result = ai.chat_json(SYSTEM_PROMPT, user)

    # Normalización defensiva
    tags = [str(t).strip().lstrip("#").lower() for t in result.get("tags", []) if str(t).strip()]
    hashtags = [("#" + str(h).strip().lstrip("#").replace(" ", "")) for h in result.get("hashtags", []) if str(h).strip()]
    out = {
        "title": str(result.get("title", ""))[:100],
        "title_alternatives": [str(t)[:100] for t in result.get("title_alternatives", [])][:5],
        "keyword": str(result.get("keyword", "")),
        "description": str(result.get("description", ""))[:5000],
        "tags": tags[:60],
        "hashtags": hashtags[:8],
        "transcript_source": source,
        "transcript_chars": len(transcript),
    }

    h = MetadataHistory(
        own_channel_id=c.id, video_id=body.video_id, title=out["title"], description=out["description"],
        tags=out["tags"], hashtags=out["hashtags"], applied=False, created_by_role=role,
    )
    db.add(h)
    db.commit()
    out["history_id"] = h.id
    out["quota"] = yt.quota_today(db)
    return out


class ApplyBody(BaseModel):
    own_channel_id: int
    video_id: str
    title: str
    description: str
    tags: list[str]
    history_id: int | None = None


@router.post("/apply")
def apply(body: ApplyBody, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    """Escribe título, descripción y etiquetas al video con videos.update (50 unidades)."""
    c = _channel(db, body.own_channel_id)
    token = yt.get_valid_token(c, db)
    if not body.title.strip():
        raise HTTPException(400, "El título no puede estar vacío")
    yt.update_video_metadata(db, token, body.video_id, body.title.strip(), body.description, body.tags)

    h = db.get(MetadataHistory, body.history_id) if body.history_id else None
    if not h:
        h = MetadataHistory(own_channel_id=c.id, video_id=body.video_id, created_by_role=role)
        db.add(h)
    h.title, h.description, h.tags, h.applied = body.title.strip(), body.description, body.tags, True
    db.commit()
    return {"ok": True, "quota": yt.quota_today(db)}


@router.get("/history/{own_channel_id}")
def history(own_channel_id: int, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    rows = (
        db.query(MetadataHistory).filter(MetadataHistory.own_channel_id == own_channel_id)
        .order_by(MetadataHistory.created_at.desc()).limit(50).all()
    )
    return [
        {"id": r.id, "video_id": r.video_id, "title": r.title, "applied": r.applied,
         "created_by_role": r.created_by_role, "created_at": r.created_at}
        for r in rows
    ]


@router.get("/costs")
def costs(role: str = Depends(require_editor), db: Session = Depends(get_db)):
    return {"generate": COST_GENERATE, "apply": COST_APPLY, "quota": yt.quota_today(db)}
