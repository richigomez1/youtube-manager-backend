"""
YOUTUBE MANAGER — backend
Patrón: todo lo compartido vive aquí; cada router hace `from main import *`.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# ───────────────────────── Config (variables de entorno) ─────────────────────────
DATABASE_URL = os.environ["DATABASE_URL"]
SECRET_KEY = os.environ["SECRET_KEY"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
TEAM_PASSWORD = os.environ["TEAM_PASSWORD"]

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

CRON_SECRET = os.environ.get("CRON_SECRET", "")

TOKEN_HOURS = 24 * 7  # sesión de una semana

# ───────────────────────── Base de datos ─────────────────────────
# DATABASE_URL puede ser la URL de la instancia tal cual la da Render (apunta a burdier_db).
# Aquí SIEMPRE forzamos nuestra propia base: youtube_manager. Zentrix/Burdier no se tocan.
from sqlalchemy.engine import make_url  # noqa: E402

DB_NAME = "youtube_manager"

_url = make_url(DATABASE_URL.strip())
if _url.drivername in ("postgres", "postgresql"):
    _url = _url.set(drivername="postgresql+psycopg")
_url = _url.set(database=DB_NAME)
_db_url = _url  # URL final, con la base correcta

engine = create_engine(_db_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_database_exists() -> None:
    """
    Si la base 'youtube_manager' no existe todavía (primer deploy), la crea.
    Se conecta a la base 'postgres' de la misma instancia y ejecuta CREATE DATABASE.
    Así no hace falta psql ni terminal.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    try:
        with engine.connect():
            print(f"[startup] Conectado a la base '{DB_NAME}'")
            return  # ya existe
    except OperationalError as e:
        if "does not exist" not in str(e):
            raise
    admin_engine = create_engine(_db_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB_NAME}"'))
        # Limpieza de un intento fallido anterior que creó una base llamada "None"
        conn.execute(text('DROP DATABASE IF EXISTS "None"'))
    admin_engine.dispose()
    print(f"[startup] Base de datos '{DB_NAME}' creada")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ───────────────────────── App ─────────────────────────
app = FastAPI(title="YouTube Manager", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:5173", "http://localhost:3000"],
    allow_origin_regex=r"^(chrome-extension://.*|https://.*\.vercel\.app)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ───────────────────────── Auth con roles ─────────────────────────
ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"


def create_token(role: str) -> str:
    payload = {
        "role": role,
        "exp": now_utc() + timedelta(hours=TOKEN_HOURS),
        "iat": now_utc(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Sesión expirada, vuelve a entrar")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Token inválido")


def current_role(authorization: Optional[str] = Header(None)) -> str:
    """Lee `Authorization: Bearer <token>` y devuelve el rol."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Falta el token")
    payload = decode_token(authorization.split(" ", 1)[1].strip())
    role = payload.get("role")
    if role not in (ROLE_ADMIN, ROLE_EDITOR):
        raise HTTPException(401, "Rol desconocido")
    return role


def require_editor(role: str = Depends(current_role)) -> str:
    """Editor o admin: herramientas de título/descripción/etiquetas."""
    return role


def require_admin(role: str = Depends(current_role)) -> str:
    """Solo admin: analytics, investigación, configuración, cuenta de YouTube."""
    if role != ROLE_ADMIN:
        raise HTTPException(403, "Solo el administrador puede hacer esto")
    return role


def require_cron(x_cron_secret: Optional[str] = Header(None), secret: Optional[str] = Query(None)) -> None:
    """Protege los endpoints que dispara el ping externo programado (cabecera X-Cron-Secret o ?secret=)."""
    given = x_cron_secret or secret
    if not CRON_SECRET or given != CRON_SECRET:
        raise HTTPException(403, "Cron secret inválido")


# ───────────────────────── Endpoints base ─────────────────────────
@app.get("/")
def root():
    return {"app": "YouTube Manager", "status": "ok"}


@app.get("/health")
def health():
    """Endpoint que despierta el servicio en Render (tier gratis)."""
    return {"ok": True, "time": now_utc().isoformat()}


@app.on_event("startup")
def on_startup():
    # Crea las tablas que falten. Cambios de columnas en tablas existentes
    # se hacen a mano (o con una migración) — create_all no altera tablas ya creadas.
    from models import Base
    ensure_database_exists()
    Base.metadata.create_all(bind=engine)
    migrate_columns()


def migrate_columns() -> None:
    """
    create_all no añade columnas a tablas ya creadas. Aquí se añaden las nuevas
    con ADD COLUMN IF NOT EXISTS (idempotente: se puede ejecutar en cada arranque).
    """
    from sqlalchemy import text
    stmts = [
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS is_short BOOLEAN DEFAULT FALSE",
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS fire_level INTEGER DEFAULT 0",
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS keyword VARCHAR(255) DEFAULT ''",
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT ''",
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS views_at_3d INTEGER",
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS views_at_7d INTEGER",
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS first_fire_at TIMESTAMPTZ",
        "ALTER TABLE tracked_channels ADD COLUMN IF NOT EXISTS channel_created_at TIMESTAMPTZ",
        "ALTER TABLE tracked_channels ADD COLUMN IF NOT EXISTS total_views INTEGER DEFAULT 0",
        "ALTER TABLE tracked_channels ADD COLUMN IF NOT EXISTS video_count INTEGER DEFAULT 0",
        "ALTER TABLE tracked_channels ADD COLUMN IF NOT EXISTS language VARCHAR(5) DEFAULT ''",
        "ALTER TABLE tracked_channels ADD COLUMN IF NOT EXISTS subs_gained_7d INTEGER DEFAULT 0",
        "ALTER TABLE tracked_channels ADD COLUMN IF NOT EXISTS views_gained_7d INTEGER DEFAULT 0",
        "ALTER TABLE tracked_channels ADD COLUMN IF NOT EXISTS handle VARCHAR(100) DEFAULT ''",
        "ALTER TABLE rotating_templates ADD COLUMN IF NOT EXISTS language VARCHAR(5) DEFAULT 'en'",
        "ALTER TABLE rotating_templates ADD COLUMN IF NOT EXISTS date_offset_days INTEGER DEFAULT 1",
        "ALTER TABLE rotating_templates ADD COLUMN IF NOT EXISTS tags_template TEXT DEFAULT ''",
        "ALTER TABLE rotating_templates ALTER COLUMN video_id DROP NOT NULL",
        "ALTER TABLE rotating_templates ALTER COLUMN own_channel_id DROP NOT NULL",
    ]
    with engine.begin() as conn:
        for st in stmts:
            conn.execute(text(st))


# ───────────────────────── Routers (al final: evita import circular) ─────────────────────────
from routers import auth, own_channels, metadata, monitor, templates  # noqa: E402

app.include_router(auth.router)
app.include_router(own_channels.router)
app.include_router(metadata.router)
app.include_router(monitor.router)
app.include_router(templates.router)
