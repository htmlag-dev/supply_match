"""Настройки проекта. Меняйте здесь модель, порт и пути."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"
WEB_DIR = BASE_DIR / "app" / "web"

DEFAULT_MODEL = "gemini-2.5-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ENV_KEY = os.environ.get("GEMINI_API_KEY", "")  # можно задать ключ переменной окружения
STATUSES = ["новый", "приглашён", "ответил", "отказ"]

PROVIDERS = {
    "auto": {"name": "Встроенный ИИ", "model": "auto", "url": "", "env": "", "web": False},
    "openrouter": {"name": "OpenRouter", "model": "openrouter/free", "url": "https://openrouter.ai/api/v1/chat/completions", "env": "OPENROUTER_API_KEY", "web": False},
    "groq": {"name": "Groq", "model": "openai/gpt-oss-20b", "url": "https://api.groq.com/openai/v1/chat/completions", "env": "GROQ_API_KEY", "web": False},
    "gemini": {"name": "Google Gemini", "model": DEFAULT_MODEL, "url": GEMINI_URL, "env": "GEMINI_API_KEY", "web": True},
    "custom": {"name": "Совместимый API", "model": "", "url": "", "env": "CUSTOM_AI_API_KEY", "web": False},
    "anthropic": {"name": "Anthropic Claude", "model": "", "url": "https://api.anthropic.com/v1/messages", "env": "ANTHROPIC_API_KEY", "web": False},
    "local": {"name": "Локальная Qwen3", "model": "supplymatch-qwen3", "url": "http://127.0.0.1:8766/v1/chat/completions", "env": "", "web": False},
}
