"""Celery application configuration.

The Celery app is configured from the project's Settings (REDIS_URL).
Broker and result backend both point to Redis.

Usage in worker process:
    celery -A omnibase.workers.app.celery_app worker --loglevel=info
"""

from __future__ import annotations

from celery import Celery

from omnibase.core.config import get_settings
from omnibase.core.logging import get_logger

log = get_logger(__name__)

settings = get_settings()

celery_app = Celery(
    "omnibase",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

# Configure serialization (JSON for safe cross-language interop)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    # Task routing: all tasks go to the default queue for now
    task_default_queue="omnibase.ingest",
    # Route CPU-heavy rebuilds away from latency-sensitive document ingestion.
    task_routes={"backfill_document_v2_task": {"queue": "omnibase.backfill"}},
    # Worker prefetch multiplier (1 for fair scheduling)
    worker_prefetch_multiplier=1,
    # Tasks acknowledge late: only ack after task completes (not on receipt)
    task_acks_late=True,
    # Reject on worker lost: worker crash requeues the task
    task_reject_on_worker_lost=True,
    # Max retries per task (bounded to prevent infinite retry loops)
    task_default_retry_delay=60,  # 60 seconds before first retry
    task_max_retries=3,
    # Soft time limit (task gets SoftTimeLimitExceeded before hard kill)
    task_soft_time_limit=300,  # 5 minutes
    task_time_limit=600,  # 10 minutes hard limit
)

log.info(
    "celery.app_configured",
    broker="redis",
    backend="redis",
    default_queue="omnibase.ingest",
)

# Late import: registers Celery tasks from tasks.py with celery_app.
# Safe here because celery_app is fully constructed, avoiding circular-init
# hazards (tasks.py imports celery_app from this module).
import omnibase.workers.tasks  # noqa: F401, E402

__all__ = ["celery_app"]
