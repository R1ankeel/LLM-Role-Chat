"""Endpoints for memory job observability."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .. import models
from .. import schemas
from .. import task_queue
from ..database import get_async_db

router = APIRouter(prefix="/jobs", tags=["memory-jobs"])


@router.get(
    "/chats/{chat_id}/memory-jobs",
    response_model=list[schemas.MemoryJobRead],
)
async def get_memory_jobs(
    chat_id: int,
    status: str | None = Query(None, description="Filter by job status"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_async_db),
):
    """List memory jobs for a chat with optional status filter."""
    if await crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Чат не найден",
        )

    query = select(models.MemoryJob).filter(models.MemoryJob.chat_id == chat_id)
    if status:
        query = query.filter(models.MemoryJob.status == status)
    query = query.order_by(models.MemoryJob.created_at.desc()).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get(
    "/memory-jobs/{job_id}",
    response_model=schemas.MemoryJobRead,
)
async def get_memory_job(
    job_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a specific memory job by ID."""
    job = await db.get(models.MemoryJob, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    return job


@router.post(
    "/memory-jobs/{job_id}/retry",
    response_model=schemas.MemoryJobRead,
)
async def retry_memory_job(
    job_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    """Manually retry a failed or dead-letter job."""
    job = await task_queue.memory_job_queue.retry_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found or not retryable",
        )
    return job


@router.post(
    "/memory-jobs/cleanup",
    status_code=status.HTTP_200_OK,
)
async def cleanup_old_jobs(
    days: int = Query(30, ge=1, le=365, description="Retention period in days"),
):
    """Remove completed/failed jobs older than retention period."""
    deleted = await task_queue.memory_job_queue.cleanup_old_jobs(days)
    return {"deleted": deleted}