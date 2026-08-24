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
# Render entrega "postgres://"; SQLAlchemy 2 + psycopg3 necesita "postgresql+psycopg://"
_db_url = DATABASE_URL
for prefix in ("postgres://", "postgresql://"):
    if _db_url.startswith(prefix):
        _db_url = "postgresql+psycopg://" + _db_url[len(prefix):]
        break

engine = create_engine(_db_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_database_exists() -> None:
    """
    Si la base de datos de la URL no existe todavía (primer deploy), la crea.
    Se conecta a la base 'postgres' de la misma instancia y ejecuta CREATE DATABASE.
    Así no hace falta psql ni terminal.
    """
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import OperationalError

    try:
        with engine.connect():
            return  # ya existe
    except OperationalError as e:
        if "does not exist" not in str(e):
            raise
    url = make_url(_db_url)
    dbname = url.database
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    admin_engine.dispose()
    print(f"[startup] Base de datos '{dbname}' creada")


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


def require_cron(x_cron_secret: Optional[str] = Header(None)) -> None:
    """Protege los endpoints que dispara el ping externo programado."""
    if not CRON_SECRET or x_cron_secret != CRON_SECRET:
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


# ───────────────────────── Routers (al final: evita import circular) ─────────────────────────
from routers import auth, own_channels  # noqa: E402

app.include_router(auth.router)
app.include_router(own_channels.router)
