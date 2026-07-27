"""Точка входа FastAPI-приложения «AI Roleplay Chat»."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import models  # noqa: F401 — регистрирует ORM-модели в Base.metadata
from database import Base, engine
from ollama_client import OLLAMA_BASE_URL
from routers import characters, chat_engine, chats

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------- Lifespan ---------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Создаёт таблицы и проверяет доступность Ollama при старте."""
    Base.metadata.create_all(bind=engine)
    logger.info("Таблицы БД созданы.")

    # Проверка доступности Ollama
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                models_list = resp.json().get("models", [])
                logger.info(
                    "Ollama доступна (%d моделей): %s",
                    len(models_list),
                    [m.get("name") for m in models_list[:5]],
                )
            else:
                logger.warning("Ollama ответила статусом %d", resp.status_code)
    except Exception:
        logger.warning(
            "Ollama НЕ доступна на %s. "
            "Запустите: ollama serve (или проверьте, что сервер запущен).",
            OLLAMA_BASE_URL,
        )
    yield


# --------------------------- App ---------------------------
app = FastAPI(title="AI Roleplay Chat", version="0.1.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API роутер
api_router = APIRouter(prefix="/api")


@api_router.get("/health")
def health_check() -> dict:
    return {"status": "ok"}


api_router.include_router(chats.router)
api_router.include_router(characters.router)
api_router.include_router(chat_engine.router)

app.include_router(api_router)

# Статика
app.mount("/static", StaticFiles(directory="static"), name="static")

# Корень
@app.get("/")
async def root():
    return FileResponse(Path("static") / "index.html")


@app.get("/chat/{chat_id}")
async def root_with_chat(chat_id: int):
    return FileResponse(Path("static") / "index.html")


# ===== Запуск с uvicorn напрямую =====
# python -m uvicorn main:app --host 0.0.0.0 --port 8000