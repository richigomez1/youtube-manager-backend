# YouTube Manager — Backend

Proyecto separado de Zentrix. No comparte ni un archivo con ella.

## Estructura

```
main.py              config, DB, auth con roles, app (los routers hacen `from main import *`)
models.py            esquema completo (Fase 1 + Fase 2) — se crea solo al arrancar
youtube_api.py       cliente de YouTube Data API v3 + contador de cuota
routers/auth.py      POST /auth/login  → {token, role}
routers/own_channels.py   nichos, canales propios, OAuth de YouTube
```

## 1. Base de datos (Render)

En la instancia Postgres existente de Render → pestaña **Shell** (o psql) → crear una base nueva:

```sql
CREATE DATABASE youtube_manager;
```

La `DATABASE_URL` de este proyecto es la misma URL interna de Render pero terminando en `/youtube_manager` en vez de la base de Zentrix.

## 2. Google Cloud Console (OAuth)

1. Crear proyecto nuevo (ej. `youtube-manager`).
2. **APIs y servicios → Biblioteca** → habilitar **YouTube Data API v3**.
3. **Pantalla de consentimiento OAuth** → tipo *Externo* → rellenar nombre y correo.
   - Ámbitos: agregar `https://www.googleapis.com/auth/youtube.force-ssl`.
   - Usuarios de prueba: agregar la cuenta de Google dueña de los canales.
4. **Credenciales → Crear credenciales → ID de cliente OAuth** → tipo *Aplicación web*.
   - URI de redirección autorizada: `https://TU-BACKEND.onrender.com/own-channels/oauth/callback`
   - Copiar `GOOGLE_CLIENT_ID` y `GOOGLE_CLIENT_SECRET`.

> **Importante:** mientras la app esté en estado *Testing*, Google caduca los refresh tokens a los 7 días
> y habría que reconectar los canales cada semana. Cuando funcione, pasar la app a **Producción**
> (botón "Publicar aplicación"). Google mostrará un aviso de "app no verificada" al conectar —
> es normal para uso propio, se acepta con "Avanzado → Ir a YouTube Manager" — y los tokens dejan de caducar.

## 3. Render (web service)

- New → Web Service → conectar el repo `youtube-manager-backend`.
- Runtime: Python. Build: `pip install -r requirements.txt`. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
- Environment: todas las variables de `.env.example`.
- Tier gratis para validar. El servicio se duerme; `/health` lo despierta (~30 s).

## 4. Probar

- `GET /health` → `{"ok": true}`
- `GET /docs` → Swagger. Login con `POST /auth/login` y pegar el token en *Authorize* (`Bearer <token>`).
- Admin: `GET /own-channels/oauth/start` → abrir la URL → autorizar con la cuenta dueña →
  vuelve al frontend con `?connected=ID`. Luego `GET /own-channels/{id}/test` confirma el token.

## Roles

| Endpoint | admin | editor |
| --- | --- | --- |
| `/auth/*`, `GET /niches`, `GET /own-channels` | ✓ | ✓ |
| OAuth, `PUT/DELETE` canales, `POST/PUT` nichos, `/quota` | ✓ | ✗ |

Los tokens de YouTube nunca salen del backend: el frontend solo ve id, título, miniatura y nicho.

## Cuota

`quota_log` registra unidades y búsquedas por día (hora del Pacífico). `GET /quota` lo muestra.
Límites: 10,000 unidades/día, 100 `search.list`/día.
