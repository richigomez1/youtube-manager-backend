"""
Cliente de IA. Usa OPENAI_API_KEY si existe; si no, OPENROUTER_API_KEY.
Todas las generaciones piden JSON y lo devuelven ya parseado.
"""
import json
import os

import requests
from fastapi import HTTPException

from main import OPENAI_API_KEY, OPENROUTER_API_KEY

# Modelo configurable por variable de entorno (AI_MODEL). Por defecto uno barato y bueno en español.
AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")


def _endpoint() -> tuple[str, dict, str]:
    if OPENAI_API_KEY:
        return (
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {OPENAI_API_KEY}"},
            AI_MODEL,
        )
    if OPENROUTER_API_KEY:
        model = AI_MODEL if "/" in AI_MODEL else f"openai/{AI_MODEL}"
        return (
            "https://openrouter.ai/api/v1/chat/completions",
            {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "HTTP-Referer": "https://youtube-manager", "X-Title": "YouTube Manager"},
            model,
        )
    raise HTTPException(500, "Falta OPENAI_API_KEY u OPENROUTER_API_KEY en el backend")


def chat_json(system: str, user: str, temperature: float = 0.6, max_tokens: int = 2500) -> dict:
    url, headers, model = _endpoint()
    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    r = requests.post(url, headers={**headers, "Content-Type": "application/json"}, json=body, timeout=120)
    if r.status_code != 200:
        raise HTTPException(502, f"La IA respondió con error: {r.text[:300]}")
    text = r.json()["choices"][0]["message"]["content"]
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise HTTPException(502, "La IA no devolvió un JSON válido; vuelve a intentar")
