"""
Función B — Descripciones rotativas (plantillas con variables).
Las plantillas se editan en la web app; la extensión de Chrome las rellena dentro de YouTube Studio.
Sin IA: texto fijo + variables. La "frase del día" se escribe una vez y sale en los 12 signos.
"""
from datetime import datetime, timedelta, timezone
import re

from pydantic import BaseModel

from main import *
from models import OwnChannel, RotatingTemplate, Setting
import youtube_api as yt

router = APIRouter(prefix="/templates", tags=["templates"])

# ───────────────────────── Signos y fechas por idioma ─────────────────────────
SIGNS = {
    "es": ["Aries", "Tauro", "Géminis", "Cáncer", "Leo", "Virgo", "Libra", "Escorpio", "Sagitario", "Capricornio", "Acuario", "Piscis"],
    "en": ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"],
    "pt": ["Áries", "Touro", "Gêmeos", "Câncer", "Leão", "Virgem", "Libra", "Escorpião", "Sagitário", "Capricórnio", "Aquário", "Peixes"],
}
SIGN_EMOJI = ["♈️", "♉️", "♊️", "♋️", "♌️", "♍️", "♎️", "♏️", "♐️", "♑️", "♒️", "♓️"]
MONTHS = {
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "pt": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"],
}
WEEKDAYS = {
    "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "pt": ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"],
}
# Hora local de referencia para "hoy" (República Dominicana, UTC-4)
LOCAL_UTC_OFFSET_HOURS = -4


def local_today() -> datetime:
    return (datetime.now(timezone.utc) + timedelta(hours=LOCAL_UTC_OFFSET_HOURS)).replace(tzinfo=None)


def build_variables(language: str, sign_index: int | None, date: datetime) -> dict:
    lang = language if language in SIGNS else "en"
    mes = MONTHS[lang][date.month - 1]
    mes_may = mes[:1].upper() + mes[1:]
    if lang == "en":
        fecha = f"{mes} {date.day} {date.year}"          # August 26 2026 (formato del canal EN)
        fecha_larga = f"{WEEKDAYS[lang][date.weekday()]}, {mes} {date.day}, {date.year}"
    elif lang == "pt":
        fecha = f"{date.day} {mes_may} {date.year}"      # 26 Agosto 2026 (formato del canal PT)
        fecha_larga = f"{WEEKDAYS[lang][date.weekday()]}, {date.day} de {mes} de {date.year}"
    else:
        fecha = f"{date.day} De {mes_may} {date.year}"   # 26 De Agosto 2026 (formato del canal ES)
        fecha_larga = f"{WEEKDAYS[lang][date.weekday()].capitalize()} {date.day} De {mes_may} {date.year}"
    v = {
        "fecha": fecha,
        "fecha_may": fecha.upper(),
        "fecha_larga": fecha_larga,
        "dia": str(date.day),
        "mes": mes,
        "mes_may": mes_may,
        "MES": mes.upper(),
        "mes_num": f"{date.month:02d}",
        "anio": str(date.year),
        "dia_semana": WEEKDAYS[lang][date.weekday()],
        "dia_semana_may": WEEKDAYS[lang][date.weekday()].capitalize(),
        "fecha_corta": date.strftime("%d/%m/%Y"),
    }
    if sign_index is not None:
        name = SIGNS[lang][sign_index]
        v.update({
            "signo": name,
            "signo_min": name.lower(),
            "signo_may": name.upper(),
            "emoji": SIGN_EMOJI[sign_index],
            "signo_en": SIGNS["en"][sign_index],          # útil para hashtags en inglés en cualquier idioma
            "signo_en_min": SIGNS["en"][sign_index].lower(),
        })
    return v


def render(text: str, variables: dict) -> str:
    def sub(m):
        return str(variables.get(m.group(1), m.group(0)))
    return re.sub(r"\{(\w+)\}", sub, text or "")


VARIABLES_HELP = [
    ("{signo}", "Libra / Escorpio"), ("{signo_min}", "libra"), ("{signo_may}", "LIBRA"), ("{emoji}", "♎️"),
    ("{signo_en}", "Libra (siempre en inglés)"), ("{signo_en_min}", "libra (inglés)"),
    ("{fecha}", "EN: September 18 2026 · PT: 18 Setembro 2026 · ES: 18 De Septiembre 2026"), ("{fecha_may}", "SEPTEMBER 18 2026"), ("{fecha_larga}", "Viernes 18 De Septiembre 2026"),
    ("{dia}", "18"), ("{mes}", "September / septiembre"), ("{mes_may}", "Septiembre"), ("{MES}", "SEPTEMBER"), ("{mes_num}", "09"), ("{anio}", "2026"),
    ("{dia_semana}", "Friday / viernes"), ("{dia_semana_may}", "Viernes"), ("{fecha_corta}", "18/09/2026"),
    ("{frase}", "La frase del día: se escribe una vez y sale en los 12 signos"),
]


# ───────────────────────── CRUD ─────────────────────────
def _out(t: RotatingTemplate) -> dict:
    all_text = (t.title_template or "") + (t.description_template or "") + (t.tags_template or "")
    return {
        "id": t.id, "own_channel_id": t.own_channel_id, "channel_title": t.own_channel.title if t.own_channel else None,
        "name": t.name, "language": t.language, "date_offset_days": t.date_offset_days,
        "title_template": t.title_template, "description_template": t.description_template,
        "tags_template": t.tags_template,
        "uses_sign": "{signo" in all_text or "{emoji}" in all_text,
        "uses_phrase": "{frase}" in all_text,
        "created_at": t.created_at,
    }


class TemplateBody(BaseModel):
    own_channel_id: int | None = None
    name: str
    language: str = "en"
    date_offset_days: int = 1
    title_template: str = ""
    description_template: str = ""
    tags_template: str = ""   # etiquetas separadas por coma, admite variables


@router.get("")
def list_templates(role: str = Depends(require_editor), db: Session = Depends(get_db)):
    rows = db.query(RotatingTemplate).order_by(RotatingTemplate.name).all()
    return [_out(t) for t in rows]


@router.get("/meta")
def meta(role: str = Depends(require_editor)):
    return {"signs": SIGNS, "emojis": SIGN_EMOJI, "variables": VARIABLES_HELP}


@router.post("")
def create_template(body: TemplateBody, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    if body.own_channel_id and not db.get(OwnChannel, body.own_channel_id):
        raise HTTPException(404, "Canal no encontrado")
    t = RotatingTemplate(**body.model_dump())
    db.add(t)
    db.commit()
    return _out(t)


@router.put("/{pk}")
def update_template(pk: int, body: TemplateBody, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    t = db.get(RotatingTemplate, pk)
    if not t:
        raise HTTPException(404, "Plantilla no encontrada")
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    db.commit()
    return _out(t)


@router.delete("/{pk}")
def delete_template(pk: int, role: str = Depends(require_admin), db: Session = Depends(get_db)):
    t = db.get(RotatingTemplate, pk)
    if not t:
        raise HTTPException(404, "Plantilla no encontrada")
    db.delete(t)
    db.commit()
    return {"ok": True}


# ───────────────────────── Frase del día ─────────────────────────
def _get_phrase(db: Session, pk: int) -> dict:
    row = db.get(Setting, f"tpl_phrase_{pk}")
    return row.value if row else {"text": "", "date": None}


class PhraseBody(BaseModel):
    text: str


@router.get("/{pk}/phrase")
def get_phrase(pk: int, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    return _get_phrase(db, pk)


@router.put("/{pk}/phrase")
def set_phrase(pk: int, body: PhraseBody, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    """La frase del día: se escribe una vez y la ven todos (extensión incluida)."""
    if not db.get(RotatingTemplate, pk):
        raise HTTPException(404, "Plantilla no encontrada")
    row = db.get(Setting, f"tpl_phrase_{pk}") or Setting(key=f"tpl_phrase_{pk}", value={})
    row.value = {"text": body.text.strip(), "date": local_today().strftime("%Y-%m-%d")}
    db.add(row)
    db.commit()
    return row.value


# ───────────────────────── Render ─────────────────────────
class RenderBody(BaseModel):
    sign_index: int | None = None     # 0 = Aries … 11 = Piscis
    date: str | None = None           # YYYY-MM-DD; si no, hoy + date_offset_days


def _render_template(db: Session, t: RotatingTemplate, body: RenderBody) -> dict:
    if body.date:
        date = datetime.strptime(body.date, "%Y-%m-%d")
    else:
        date = local_today() + timedelta(days=t.date_offset_days or 0)
    v = build_variables(t.language, body.sign_index, date)
    phrase = _get_phrase(db, t.id)
    v["frase"] = phrase.get("text", "")
    tags = [x.strip() for x in render(t.tags_template, v).split(",") if x.strip()]
    return {
        "title": " ".join(render(t.title_template, v).split()),
        "description": render(t.description_template, v),
        "tags": tags,
        "date_used": date.strftime("%Y-%m-%d"),
        "phrase": phrase,
        "phrase_missing": "{frase}" in (t.title_template or "") and not phrase.get("text"),
        "variables": v,
    }


@router.post("/{pk}/render")
def render_template(pk: int, body: RenderBody, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    t = db.get(RotatingTemplate, pk)
    if not t:
        raise HTTPException(404, "Plantilla no encontrada")
    return _render_template(db, t, body)


class ApplyBody(RenderBody):
    video_id: str
    write_title: bool = False


@router.post("/{pk}/apply")
def apply_template(pk: int, body: ApplyBody, role: str = Depends(require_editor), db: Session = Depends(get_db)):
    """Escribe la plantilla ya rellenada en un video existente (videos.update, 51 unidades)."""
    t = db.get(RotatingTemplate, pk)
    if not t:
        raise HTTPException(404, "Plantilla no encontrada")
    if not t.own_channel:
        raise HTTPException(400, "La plantilla no tiene canal asignado")
    token = yt.get_valid_token(t.own_channel, db)
    r = _render_template(db, t, body)
    current = yt.get_video_snippet(db, token, body.video_id)
    title = r["title"] if (body.write_title and r["title"].strip()) else current.get("title", "")
    yt.update_video_metadata(db, token, body.video_id, title, r["description"], r["tags"] or current.get("tags", []))
    t.last_run_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, **r, "quota": yt.quota_today(db)}
