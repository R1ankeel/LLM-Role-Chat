"""Точка входа FastAPI-приложения «AI Roleplay Chat»."""

import asyncio
import json
import logging
import structlog
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import memory_service
from . import models  # noqa: F401 — регистрирует ORM-модели в Base.metadata
from . import task_queue
from . import avatar_service
from .config import settings
from .database import async_engine, Base, ensure_schema, init_db
from .routers import (
    characters,
    chat_engine,
    chats,
    debug,
    jobs,
    locations,
    relationships,
)


class JSONFormatter(logging.Formatter):
    """Render standard-library log records as single-line JSON (Sprint 4 item 4).

    Regular ``logger.info("msg")`` calls become
    ``{"timestamp", "level", "logger", "message", ...extra}``. Records produced
    by structlog's ``JSONRenderer`` (message already a JSON string) are merged,
    so structlog fields appear top-level instead of being double-wrapped.
    """

    _STANDARD_ATTRS = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
    })

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._STANDARD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        message = payload.get("message")
        if isinstance(message, str):
            try:
                parsed = json.loads(message)
                if isinstance(parsed, dict):
                    payload.update(parsed)
                    payload.pop("message", None)
            except (json.JSONDecodeError, TypeError):
                pass
        return json.dumps(payload, ensure_ascii=False, default=str)


# Configure structured logging
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logging.basicConfig(level=logging.INFO)
# Route ALL loggers (including stdlib modules like relationship_service) through
# the JSON formatter; structlog's own output is merged, not double-wrapped.
for _handler in logging.getLogger().handlers:
    _handler.setFormatter(JSONFormatter())
logger = structlog.get_logger(__name__)


MEMORY_JOBS_POLL_SECONDS = 10.0


async def _memory_jobs_worker():
    """Background task: dispatch pending/failed embedding jobs (embed/backfill).

    Only embedding jobs are auto-dispatched here; post_round jobs are always
    run synchronously right after enqueue, so a pending one is stale and must
    not be re-executed against old snapshots.
    """
    while True:
        await asyncio.sleep(MEMORY_JOBS_POLL_SECONDS)
        try:
            dispatched = await task_queue.memory_job_queue.process_pending_jobs(
                job_types=["embed_memory", "backfill_embeddings"]
            )
            if dispatched:
                logger.info("memory_jobs_dispatched", count=dispatched)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("memory_jobs_worker_error")


async def _adaptive_consolidation_pass(app: FastAPI) -> None:
    """Score-based consolidation pass (§20, Sprint 12).

    For every chat evaluate soft/hard/critical since the last consolidation and
    enqueue a background job when triggered. Idle chats (score≈0, no critical
    event) are skipped — no timer-based consolidation. Marking happens at
    enqueue time, so repeated passes for the same events do not re-trigger.
    """
    from . import crud
    from .database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        chats = await crud.get_chats(db, skip=0, limit=1000)
        for chat in chats:
            try:
                decision = await memory_service.schedule_adaptive_consolidation(
                    db,
                    chat_id=chat.id,
                    model_name=getattr(chat, "model_name", None) or None,
                )
                level = decision.get("level")
                if level == "skip":
                    continue
                job = decision.get("job")
                if job is not None:
                    asyncio.create_task(task_queue.memory_job_queue.run_job(job))
                logger.info(
                    "adaptive_consolidation_triggered",
                    chat_id=chat.id,
                    level=level,
                    score_hard=decision.get("score_hard"),
                    job_id=decision.get("job_id"),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "adaptive_consolidation_error", chat_id=chat.id
                )


async def _consolidation_scheduler(app: FastAPI):
    """Background task: score-based adaptive consolidation (Sprint 12).

    Replaces the fixed 24h timer. When ``adaptive_consolidation_enabled`` is
    on, poll every ``consolidation_poll_seconds`` and trigger by score/critical
    per chat. Otherwise fall back to the legacy interval job (canary).
    """
    while True:
        await asyncio.sleep(settings.consolidation_poll_seconds)
        if not settings.consolidation_enabled:
            continue
        if not settings.adaptive_consolidation_enabled:
            await asyncio.sleep(max(0.0, settings.consolidation_interval_hours * 3600))
            if not settings.consolidation_enabled:
                continue
            try:
                logger.info("consolidation_scheduler_triggered")
                # Enqueue global consolidation job (chat_id=0 means all chats)
                job = await memory_service.enqueue_consolidation_job(chat_id=0)
                if job:
                    asyncio.create_task(task_queue.memory_job_queue.run_job(job))
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("consolidation_scheduler_error")
            continue
        try:
            await _adaptive_consolidation_pass(app)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("adaptive_consolidation_pass_error")


# --------------------------- Lifespan ---------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Создаёт таблицы и проверяет доступность Ollama при старте."""
    # Initialize database (create tables + run migrations)
    await init_db()
    logger.info("Таблицы БД созданы и миграции применены.")

    # Гарантировать каталог аватаров (раздаётся на /static)
    avatar_service.ensure_avatar_dir()
    logger.info("Каталог аватаров готов: %s", avatar_service.avatar_dir_path())

    async with httpx.AsyncClient(
        base_url=settings.ollama_base_url, timeout=settings.generate_timeout
    ) as client:
        app.state.ollama_client = client
        try:
            resp = await client.get("/api/tags")
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
                settings.ollama_base_url,
            )

        # Start memory jobs worker (dispatches pending embed/backfill jobs)
        app.state.memory_jobs_task = asyncio.create_task(_memory_jobs_worker())
        logger.info("Memory jobs worker started (interval=%.0fs)", MEMORY_JOBS_POLL_SECONDS)

        # Start consolidation scheduler if enabled
        if settings.consolidation_enabled:
            app.state.consolidation_task = asyncio.create_task(
                _consolidation_scheduler(app)
            )
            if settings.adaptive_consolidation_enabled:
                logger.info(
                    "Adaptive consolidation scheduler started (poll=%.0fs)",
                    settings.consolidation_poll_seconds,
                )
            else:
                logger.info("Consolidation scheduler started (interval=%dh)", settings.consolidation_interval_hours)

        yield

        # Shutdown
        if hasattr(app.state, "memory_jobs_task"):
            app.state.memory_jobs_task.cancel()
            try:
                await app.state.memory_jobs_task
            except asyncio.CancelledError:
                pass
            logger.info("Memory jobs worker stopped")

        if hasattr(app.state, "consolidation_task"):
            app.state.consolidation_task.cancel()
            try:
                await app.state.consolidation_task
            except asyncio.CancelledError:
                pass
            logger.info("Consolidation scheduler stopped")


# --------------------------- App ---------------------------
app = FastAPI(title="AI Roleplay Chat", version="0.1.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API router
api_router = APIRouter(prefix="/api")


@api_router.get("/health")
def health_check() -> dict:
    return {"status": "ok"}


@api_router.get("/models")
async def list_models(request: Request) -> dict:
    """List models loaded in Ollama (sorted by name)."""
    client: httpx.AsyncClient = request.app.state.ollama_client
    try:
        resp = await client.get("/api/tags")
        if resp.status_code != 200:
            logger.warning("Ollama ответила статусом %d", resp.status_code)
            return {"models": [], "error": "Ollama ответила статусом %d" % resp.status_code}
        models_list = resp.json().get("models", [])
        names = sorted({m.get("name") for m in models_list if m.get("name")})
        return {"models": names}
    except Exception:
        logger.warning("Не удалось получить список моделей от Ollama")
        return {"models": [], "error": "Ollama недоступна"}


api_router.include_router(chats.router)
api_router.include_router(characters.router)
api_router.include_router(locations.router)
api_router.include_router(chat_engine.router)
api_router.include_router(jobs.router)
api_router.include_router(relationships.router)
api_router.include_router(debug.router)

app.include_router(api_router)

# Статика
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Корень
@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/chat/{chat_id}")
async def root_with_chat(chat_id: int):
    return FileResponse(STATIC_DIR / "index.html")


# ===== Запуск с uvicorn напрямую =====
# python -m uvicorn main:app --host 0.0.0.0 --port 8000