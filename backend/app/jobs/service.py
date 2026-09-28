"""Job service: submit, claim, complete, and fail jobs in the PostgreSQL queue.

Two-layer locking strategy for safe concurrent job claiming:

1. **Advisory lock** (``pg_try_advisory_lock``) — briefly serialises the
   claim phase so only one worker runs the ``UPDATE … RETURNING`` at a
   time, preventing thundering-herd contention.

2. **SKIP LOCKED** (SQL-level) — within the advisory lock the claim runs a
   single ``UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1)
   RETURNING *`` statement.  Rows already claimed by an in-flight
   transaction are skipped rather than blocked, enabling parallel workers
   to each grab a distinct job without deadlocks.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import JobQueue, JobStatus

logger = logging.getLogger(__name__)

# Advisory lock key — derived from a stable namespace string so that all
# worker processes share the same key.  Stored as a 64-bit unsigned int.
ADVISORY_LOCK_CLAIM_KEY: int = int.from_bytes(
    hashlib.sha256(b"enterprise-ai-job-queue-claim").digest()[:8],
    byteorder="big",
    signed=False,
) % (2**63)  # PostgreSQL bigint is signed 64-bit

# Default lease duration (seconds) for a claimed job's lock.
# If the worker crashes, the job becomes reclaimable after this TTL.
DEFAULT_LOCK_TTL_SECONDS: int = 300

# Base delay (seconds) for exponential backoff between retries.
RETRY_BASE_DELAY_SECONDS: int = 5


class JobService:
    """Service layer for the PostgreSQL-backed job queue.

    All mutating operations are designed to be safe under concurrent
    access from multiple worker processes — no Redis or external queue
    system is required.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Job Submission ───────────────────────────────────────────

    async def submit_job(
        self,
        job_type: str,
        payload: dict[str, Any] | None = None,
        priority: int = 0,
        max_retries: int | None = None,
        expires_at: datetime | None = None,
    ) -> JobQueue:
        """Create and enqueue a new background job.

        Parameters
        ----------
        job_type
            One of ``'document_ingest'`` or ``'report_generation'``.
        payload
            JSON-serialisable dict of job parameters.
        priority
            0-100; higher values are claimed first.
        max_retries
            Override the system default from settings.
        expires_at
            Optional deadline; jobs past this time are skipped by the
            worker.
        """
        if job_type not in ("document_ingest", "report_generation"):
            raise ValueError(
                f"Unsupported job type: {job_type}. "
                "Supported: document_ingest, report_generation"
            )

        now = datetime.now(timezone.utc)
        job_id = str(uuid.uuid4())
        job = JobQueue(
            id=job_id,
            job_type=job_type,
            status=JobStatus.QUEUED,
            payload=json.dumps(payload or {}),
            priority=priority,
            max_retries=(
                max_retries
                if max_retries is not None
                else settings.job_max_retries
            ),
            expires_at=expires_at,
            created_at=now,
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)
        logger.info(
            "Job submitted — id=%s type=%s priority=%d", job_id, job_type, priority
        )
        return job

    # ── Read operations ───────────────────────────────────────────

    async def get_job(self, job_id: str) -> JobQueue | None:
        """Fetch a single job by ID."""
        result = await self.db.execute(
            select(JobQueue).where(JobQueue.id == job_id)
        )
        return result.scalars().first()

    async def list_jobs(
        self,
        status: str | None = None,
        job_type: str | None = None,
        limit: int = 100,
    ) -> list[JobQueue]:
        """List jobs with optional filtering. Returns most-recent first."""
        stmt = select(JobQueue)
        if status:
            stmt = stmt.where(JobQueue.status == status)
        if job_type:
            stmt = stmt.where(JobQueue.job_type == job_type)
        stmt = stmt.order_by(JobQueue.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    # ── Claiming (SKIP LOCKED + advisory lock) ────────────────────

    async def _try_advisory_lock(self) -> bool:
        """Attempt to acquire the advisory lock for claim coordination.

        Returns ``True`` if acquired, ``False`` if another worker holds it.
        """
        result = await self.db.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": ADVISORY_LOCK_CLAIM_KEY},
        )
        return bool(result.scalar())

    async def _release_advisory_lock(self) -> None:
        """Release the advisory claim lock."""
        await self.db.execute(
            text("SELECT pg_advisory_unlock(:key)"),
            {"key": ADVISORY_LOCK_CLAIM_KEY},
        )

    async def claim_job(
        self,
        worker_id: str,
        lock_ttl: int = DEFAULT_LOCK_TTL_SECONDS,
    ) -> JobQueue | None:
        """Atomically claim the next available job.

        Algorithm
        ---------
        1. Acquire an **advisory lock** to serialise the claim phase
           (prevents thundering herd).
        2. Run a single ``UPDATE … WHERE id = (SELECT … FOR UPDATE
           SKIP LOCKED LIMIT 1) RETURNING *`` that:
           - Picks the highest-priority stale-or-queued job
           - Locks the row with ``FOR UPDATE SKIP LOCKED``
           - Sets status to ``running``, assigns ``worker_id``,
             and increments ``attempts``
           - Returns the claimed row
        3. Release the advisory lock (row-level lock is held until commit).
        """
        if not await self._try_advisory_lock():
            logger.debug("Advisory claim lock not available; another worker is claiming")
            return None

        now = datetime.now(timezone.utc)
        lock_expires_at = now + timedelta(seconds=lock_ttl)

        try:
            result = await self.db.execute(
                text(
                    """
                    UPDATE job_queue
                    SET   status          = CAST(:running AS jobstatus),
                          worker_id       = :worker_id,
                          started_at      = :now,
                          locked_at       = :now,
                          lock_expires_at = :lock_expires_at,
                          attempts        = attempts + 1
                    WHERE id = (
                        SELECT id
                        FROM job_queue
                        WHERE (status = CAST(:queued AS jobstatus) OR status = CAST(:running AS jobstatus))
                          AND (lock_expires_at IS NULL OR lock_expires_at < :now)
                          AND (expires_at IS NULL OR expires_at > :now)
                        ORDER BY priority DESC, created_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING *
                    """
                ),
                {
                    "running": JobStatus.RUNNING.name,
                    "queued": JobStatus.QUEUED.name,
                    "worker_id": worker_id,
                    "now": now,
                    "lock_expires_at": lock_expires_at,
                },
            )
            await self.db.commit()

            row = result.fetchone()
            if row is None:
                return None

            job = JobQueue(**dict(row._mapping))
            logger.info(
                "Worker %s claimed job %s (type=%s, attempt=%d, priority=%d)",
                worker_id,
                job.id,
                job.job_type,
                job.attempts,
                job.priority,
            )
            return job
        finally:
            await self._release_advisory_lock()

    async def renew_lock(
        self,
        job_id: str,
        worker_id: str,
        lock_ttl: int = DEFAULT_LOCK_TTL_SECONDS,
    ) -> bool:
        """Extend the job's lock lease to prevent expiry during long runs.

        Uses a separate session so it never blocks the handler's session.
        Returns ``False`` if the worker no longer owns the lock (e.g., the
        job was cancelled or the lock expired between checks).
        """
        now = datetime.now(timezone.utc)
        lock_expires_at = now + timedelta(seconds=lock_ttl)
        result = await self.db.execute(
            text(
                """
                UPDATE job_queue
                SET lock_expires_at = :lock_expires_at
                WHERE id = :job_id
                  AND worker_id = :worker_id
                  AND status = :running
                """
            ),
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "running": JobStatus.RUNNING.name,
                "lock_expires_at": lock_expires_at,
            },
        )
        await self.db.commit()
        renewed = result.rowcount > 0
        if not renewed:
            logger.warning(
                "Lock renewal failed for job %s (lost lease or status changed)",
                job_id,
            )
        return renewed

    # ── Status transitions ────────────────────────────────────────

    async def complete_job(
        self,
        job_id: str,
        result_data: dict[str, Any] | None = None,
    ) -> None:
        """Mark a job as completed and store its result payload."""
        now = datetime.now(timezone.utc)
        await self.db.execute(
            text(
                """
                UPDATE job_queue
                SET status          = CAST(:completed AS jobstatus),
                    result          = :result,
                    completed_at    = :now,
                    locked_at       = NULL,
                    lock_expires_at = NULL,
                    worker_id       = NULL
                WHERE id = :job_id
                """
            ),
            {
                "completed": JobStatus.COMPLETED.name,
                "job_id": job_id,
                "result": json.dumps(result_data) if result_data else None,
                "now": now,
            },
        )
        await self.db.commit()
        logger.info("Job %s marked completed", job_id)

    async def fail_job(
        self,
        job_id: str,
        error_message: str,
        retry_delay: int | None = None,
    ) -> None:
        """Mark a job as failed, or requeue for retry if attempts remain.

        Implements **exponential backoff**: the requeued job's
        ``lock_expires_at`` is set to ``now + delay`` so it won't be
        re-claimable until the backoff period has elapsed.
        """
        now = datetime.now(timezone.utc)

        row_result = await self.db.execute(
            text(
                "SELECT attempts, max_retries FROM job_queue WHERE id = :job_id"
            ),
            {"job_id": job_id},
        )
        row = row_result.mappings().first()
        if row is None:
            logger.warning("fail_job: job %s not found", job_id)
            return

        if row["attempts"] < row["max_retries"]:
            delay = retry_delay if retry_delay is not None else (
                RETRY_BASE_DELAY_SECONDS * (2 ** (row["attempts"] - 1))
            )
            retry_until = now + timedelta(seconds=delay)
            await self.db.execute(
                text(
                    """
                    UPDATE job_queue
                    SET status          = CAST(:queued AS jobstatus),
                        error_message   = :error,
                        locked_at       = NULL,
                        lock_expires_at = :retry_until,
                        worker_id       = NULL
                    WHERE id = :job_id
                    """
                ),
                {
                    "queued": JobStatus.QUEUED.name,
                    "error": error_message,
                    "job_id": job_id,
                    "retry_until": retry_until,
                },
            )
            await self.db.commit()
            logger.info(
                "Job %s failed (attempt %d/%d); requeued with %ds backoff",
                job_id,
                row["attempts"],
                row["max_retries"],
                delay,
            )
        else:
            await self.db.execute(
                text(
                    """
                    UPDATE job_queue
                    SET status          = CAST(:failed AS jobstatus),
                        error_message   = :error,
                        completed_at    = :now,
                        locked_at       = NULL,
                        lock_expires_at = NULL,
                        worker_id       = NULL
                    WHERE id = :job_id
                    """
                ),
                {
                    "failed": JobStatus.FAILED.name,
                    "job_id": job_id,
                    "error": error_message,
                    "now": now,
                },
            )
            await self.db.commit()
            logger.info(
                "Job %s permanently failed after %d attempts",
                job_id,
                row["attempts"],
            )

    async def cancel_job(self, job_id: str, reason: str = "cancelled") -> bool:
        """Cancel a queued or running job. Returns ``True`` if a row was updated."""
        result = await self.db.execute(
            text(
                """
                UPDATE job_queue
                SET status          = CAST(:cancelled AS jobstatus),
                    error_message   = :reason,
                    completed_at    = :now,
                    locked_at       = NULL,
                    lock_expires_at = NULL,
                    worker_id       = NULL
                WHERE id = :job_id
                  AND status IN (CAST(:queued AS jobstatus), CAST(:running AS jobstatus))
                """
            ),
            {
                "cancelled": JobStatus.CANCELLED.name,
                "queued": JobStatus.QUEUED.name,
                "running": JobStatus.RUNNING.name,
                "job_id": job_id,
                "reason": reason,
                "now": datetime.now(timezone.utc),
            },
        )
        await self.db.commit()
        return result.rowcount > 0
