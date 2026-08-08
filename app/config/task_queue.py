"""Настройки task queue для memory-джобов (Sprint 2, §4.9)."""

from pydantic import Field




class TaskQueueSettings():
    """Task Queue for Memory Jobs (P3)."""

    # Task Queue for Memory Jobs (P3)
    task_queue_enabled: bool = Field(default=True, alias="TASK_QUEUE_ENABLED")
    task_queue_max_retries: int = Field(default=3, alias="TASK_QUEUE_MAX_RETRIES")
    task_queue_retry_min_wait: float = Field(default=5.0, alias="TASK_QUEUE_RETRY_MIN_WAIT")
    task_queue_retry_max_wait: float = Field(default=60.0, alias="TASK_QUEUE_RETRY_MAX_WAIT")
    task_queue_retry_multiplier: float = Field(default=2.0, alias="TASK_QUEUE_RETRY_MULTIPLIER")
    task_queue_max_concurrent: int = Field(default=5, alias="TASK_QUEUE_MAX_CONCURRENT")
    task_queue_retention_days: int = Field(default=30, alias="TASK_QUEUE_RETENTION_DAYS")
