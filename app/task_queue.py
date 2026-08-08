"""Lightweight async task queue with retry, persistence, and observability."""

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

import structlog
from sqlalchemy import delete, select
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from . import crud
from . import embedding_service
from . import models
from .config import settings
from .database import AsyncSessionLocal

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Handler-registry (Sprint 1, §7.1 decomposition.md)
# ---------------------------------------------------------------------------
# Диспетчер джобов НЕ знает обработчики напрямую: обработчики регистрируются
# (паттерн handler-registry), направление — сервис → task_queue. Цикл
# ``task_queue ↔ memory_service`` разорван: task_queue больше не импортирует
# memory_service.
_HANDLERS: dict[str, Callable[..., Awaitable[dict]]] = {}


def register_handler(
    job_type: str,
    handler: Callable[..., Awaitable[dict]],
) -> None:
    """Зарегистрировать обработчик джоба по типу (вызывается сервисом)."""
    _HANDLERS[job_type] = handler


def get_handler(job_type: str) -> Callable[..., Awaitable[dict]]:
    """Получить обработчик по типу; ValueError при неизвестном типе."""
    try:
        return _HANDLERS[job_type]
    except KeyError:
        raise ValueError(f"Unknown job type: {job_type}")


class DateTimeEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime objects."""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, cls=DateTimeEncoder)


class JobStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class MemoryJobQueue:
    """Lightweight async job queue with retry logic, persistence, and observability."""

    def __init__(self):
        self._running_jobs: dict[int, asyncio.Task] = {}
        self._semaphore: Optional[asyncio.Semaphore] = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(settings.task_queue_max_concurrent)
        return self._semaphore

    def _create_correlation_id(self) -> str:
        return str(uuid.uuid4())[:8]

    async def enqueue(
        self,
        job_type: str,
        chat_id: int,
        payload: dict[str, Any],
        *,
        max_attempts: int | None = None,
        correlation_id: str | None = None,
    ) -> models.MemoryJob:
        """Persist job to DB and return job record."""
        correlation_id = correlation_id or self._create_correlation_id()
        async with AsyncSessionLocal() as db:
            try:
                job = models.MemoryJob(
                    chat_id=chat_id,
                    job_type=job_type,
                    status=JobStatus.PENDING,
                    payload=_json_dumps(payload),
                    max_attempts=max_attempts or settings.task_queue_max_retries,
                    correlation_id=correlation_id,
                )
                db.add(job)
                await db.commit()
                await db.refresh(job)

                logger.info(
                    "job_enqueued",
                    job_id=job.id,
                    job_type=job_type,
                    chat_id=chat_id,
                    correlation_id=correlation_id,
                )
                return job
            except Exception:
                logger.exception("job_enqueue_failed", chat_id=chat_id)
                raise

    async def run_job(
        self,
        job: models.MemoryJob,
        handler: Callable[..., Awaitable[dict]] | None = None,
    ) -> None:
        """Execute job with retry logic and status updates.

        ``handler`` — обработчик payload'а; если не передан, берётся из
        handler-registry по ``job.job_type`` (Sprint 1, §7.1).
        """
        semaphore = self._get_semaphore()
        async with semaphore:
            async with AsyncSessionLocal() as db:
                try:
                    # Reload job in this session since it was created in a different session
                    job = await db.get(models.MemoryJob, job.id)
                    if not job:
                        logger.error("job_not_found", job_id=job.id)
                        return

                    payload = json.loads(job.payload)

                    # Manual retry loop with proper attempt tracking
                    last_exception = None
                    for attempt in range(1, job.max_attempts + 1):
                        # Reload job each attempt
                        job = await db.get(models.MemoryJob, job.id)
                        if not job:
                            logger.error("job_not_found", job_id=job.id)
                            return

                        job.status = JobStatus.RUNNING
                        job.started_at = datetime.utcnow()
                        job.attempt = attempt
                        await db.commit()

                        logger.info(
                            "job_started",
                            job_id=job.id,
                            job_type=job.job_type,
                            attempt=attempt,
                            correlation_id=job.correlation_id,
                        )

                        try:
                            resolver = handler or get_handler(job.job_type)
                            result = await resolver(payload)

                            # Success
                            job = await db.get(models.MemoryJob, job.id)
                            job.status = JobStatus.SUCCEEDED
                            job.completed_at = datetime.utcnow()
                            job.result = _json_dumps(result) if result else "{}"
                            await db.commit()

                            logger.info(
                                "job_succeeded",
                                job_id=job.id,
                                job_type=job.job_type,
                                duration_ms=int(
                                    (job.completed_at - job.started_at).total_seconds() * 1000
                                ),
                                correlation_id=job.correlation_id,
                            )
                            return

                        except Exception as exc:
                            last_exception = exc
                            job = await db.get(models.MemoryJob, job.id)
                            job.error_message = str(exc)
                            job.completed_at = datetime.utcnow()

                            if attempt >= job.max_attempts:
                                job.status = JobStatus.DEAD_LETTER
                                logger.error(
                                    "job_dead_letter",
                                    job_id=job.id,
                                    job_type=job.job_type,
                                    error=str(exc),
                                    correlation_id=job.correlation_id,
                                )
                            else:
                                job.status = JobStatus.FAILED
                                logger.warning(
                                    "job_failed_retry_scheduled",
                                    job_id=job.id,
                                    job_type=job.job_type,
                                    attempt=attempt,
                                    max_attempts=job.max_attempts,
                                    error=str(exc),
                                    correlation_id=job.correlation_id,
                                )
                                # Wait before retry
                                wait_time = min(
                                    settings.task_queue_retry_min_wait
                                    * (settings.task_queue_retry_multiplier ** (attempt - 1)),
                                    settings.task_queue_retry_max_wait,
                                )
                                logger.info(
                                    "job_retry_wait",
                                    job_id=job.id,
                                    wait_seconds=wait_time,
                                    correlation_id=job.correlation_id,
                                )
                                await asyncio.sleep(wait_time)

                            await db.commit()

                    # If we exhausted all retries
                    logger.error(
                        "job_exhausted_retries",
                        job_id=job.id,
                        job_type=job.job_type,
                        max_attempts=job.max_attempts,
                        correlation_id=job.correlation_id,
                    )

                finally:
                    # Job tracking cleanup handled by caller
                    pass

    async def process_pending_jobs(self, job_types: list[str] | None = None) -> int:
        """Process all pending/failed jobs (for startup recovery or cron).

        job_types optionally restricts which job types are dispatched (e.g.
        only embedding jobs) so stale jobs of other kinds are left untouched.
        """
        async with AsyncSessionLocal() as db:
            try:
                stmt = select(models.MemoryJob).filter(
                    models.MemoryJob.status.in_([JobStatus.PENDING, JobStatus.FAILED])
                )
                if job_types:
                    stmt = stmt.filter(models.MemoryJob.job_type.in_(job_types))
                stmt = stmt.order_by(models.MemoryJob.created_at).limit(100)
                result = await db.execute(stmt)
                jobs = list(result.scalars().all())

                count = 0
                for job in jobs:
                    if job.id not in self._running_jobs:
                        task = asyncio.create_task(self.run_job(job))
                        self._running_jobs[job.id] = task
                        task.add_done_callback(lambda t, jid=job.id: self._running_jobs.pop(jid, None))
                        count += 1
                return count
            finally:
                pass

    async def retry_job(self, job_id: int) -> Optional[models.MemoryJob]:
        """Manually retry a failed/dead-letter job."""
        async with AsyncSessionLocal() as db:
            try:
                job = await db.get(models.MemoryJob, job_id)
                if not job:
                    return None

                if job.status not in (JobStatus.FAILED, JobStatus.DEAD_LETTER):
                    return job

                job.status = JobStatus.PENDING
                job.attempt = 0
                job.error_message = None
                job.started_at = None
                job.completed_at = None
                await db.commit()
                await db.refresh(job)

                logger.info(
                    "job_manual_retry",
                    job_id=job.id,
                    job_type=job.job_type,
                    correlation_id=job.correlation_id,
                )
                return job
            finally:
                pass

    async def cleanup_old_jobs(self, retention_days: int | None = None) -> int:
        """Delete old completed jobs based on retention policy."""
        retention = retention_days or settings.task_queue_retention_days
        cutoff = datetime.utcnow() - timedelta(days=retention)

        async with AsyncSessionLocal() as db:
            try:
                result = await db.execute(
                    delete(models.MemoryJob).filter(
                        models.MemoryJob.status.in_([JobStatus.SUCCEEDED, JobStatus.DEAD_LETTER]),
                        models.MemoryJob.completed_at < cutoff,
                    )
                )
                deleted = result.rowcount
                await db.commit()

                if deleted:
                    logger.info("jobs_cleaned_up", count=deleted, retention_days=retention)
                return deleted
            finally:
                pass

    async def get_job_stats(self, chat_id: int | None = None) -> dict[str, int]:
        """Get job counts by status for observability."""
        async with AsyncSessionLocal() as db:
            try:
                query = select(models.MemoryJob)
                if chat_id is not None:
                    query = query.filter(models.MemoryJob.chat_id == chat_id)

                stats = {}
                for status in [
                    JobStatus.PENDING,
                    JobStatus.RUNNING,
                    JobStatus.SUCCEEDED,
                    JobStatus.FAILED,
                    JobStatus.DEAD_LETTER,
                ]:
                    result = await db.execute(
                        query.filter(models.MemoryJob.status == status)
                    )
                    count = len(list(result.scalars().all()))
                    stats[status] = count
                return stats
            finally:
                pass


memory_job_queue = MemoryJobQueue()