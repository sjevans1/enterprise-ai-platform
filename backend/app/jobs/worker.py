"""PostgreSQL-backed background job worker.

Polls the ``job_queue`` table, claims jobs using SQL-level locking
(``SKIP LOCKED``) combined with PostgreSQL advisory locks, executes
registered handlers, and updates job status through the lifecycle:

    queued → running → completed
              ↘ failed → queued (retry w/ backoff) → completed / failed

The worker runs in the **same Python venv** as the backend API server.
It connects to PostgreSQL directly via the async engine defined in
``app.db.connection``.

Usage (see ``app/jobs/runner.py`` for CLI flags)::

    .venv/bin/python -m app.jobs.runner
    .venv/bin/python -m app.jobs.runner --poll-interval 2 --lock-ttl 600
"""
from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import AsyncSessionLocal
from app.db.models import JobQueue, JobStatus
from app.jobs.handlers import JobHandler, get_handler
from app.jobs.service import DEFAULT_LOCK_TTL_SECONDS, JobService

logger = logging.getLogger(__name__)


class JobWorker:
    """Background worker that processes jobs from the PostgreSQL queue.

    Concurrency model
    -----------------
    * The worker can process up to ``max_concurrent`` jobs in parallel.
    * Each job runs in its own ``AsyncSession`` (separate transaction).
    * Lock renewal for each in-flight job runs on yet another session,
      so it never blocks the handler.
    * Graceful shutdown: on SIGINT / SIGTERM the worker stops claiming
      new jobs and waits for in-flight jobs to finish.
    """

    def __init__(
        self,
        worker_id: str | None = None,
        poll_interval: float = 5.0,
        lock_ttl: int = DEFAULT_LOCK_TTL_SECONDS,
        max_concurrent: int = 1,
    ):
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.poll_interval = poll_interval
        self.lock_ttl = lock_ttl
        self.max_concurrent = max_concurrent
        self._shutdown = False

    # ── Signal handling ─────────────────────────────────────────

    def _install_signal_handlers(self) -> None:
        """Install SIGINT / SIGTERM handlers for graceful shutdown."""

        def _handler(signum: int, _frame: Any) -> None:
            logger.info(
                "Worker %s received signal %d — initiating graceful shutdown",
                self.worker_id,
                signum,
            )
            self._shutdown = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, _handler)

    # ── Main loop ───────────────────────────────────────────────

    async def run(self) -> None:
        """Main worker loop. Runs until a shutdown signal is received."""
        self._install_signal_handlers()
        logger.info(
            "Worker %s started (poll_interval=%.1fs, lock_ttl=%ds, max_concurrent=%d)",
            self.worker_id,
            self.poll_interval,
            self.lock_ttl,
            self.max_concurrent,
        )

        active_tasks: set[asyncio.Task[None]] = set()

        while not self._shutdown:
            # ── Prune completed tasks ────────────────────────────
            active_tasks = {t for t in active_tasks if not t.done()}

            # ── Claim and dispatch if we have capacity ───────────
            if len(active_tasks) < self.max_concurrent:
                async with AsyncSessionLocal() as db:
                    service = JobService(db)
                    job = await service.claim_job(
                        self.worker_id, self.lock_ttl
                    )

                if job is not None:
                    task = asyncio.create_task(self._process_job(job))
                    active_tasks.add(task)
                    task.add_done_callback(active_tasks.discard)
                    continue  # immediately try to claim another

            # ── No job claimed — sleep before next poll ──────────
            if not self._shutdown:
                await asyncio.sleep(self.poll_interval)

        # ── Graceful shutdown: wait for in-flight jobs ────────────
        if active_tasks:
            logger.info(
                "Worker %s: waiting for %d in-flight job(s) to complete",
                self.worker_id,
                len(active_tasks),
            )
            await asyncio.gather(*active_tasks, return_exceptions=True)

        logger.info("Worker %s stopped", self.worker_id)

    # ── Job processing ──────────────────────────────────────────

    async def _process_job(self, job: JobQueue) -> None:
        """Execute a single claimed job and update its status.

        Steps:
        1. Start a background lock-renewal task (separate session).
        2. Instantiate the handler for ``job.job_type``.
        3. Run ``handler.execute(db, job)``.
        4. On success → ``complete_job``.
        5. On failure → ``fail_job`` (which may requeue w/ backoff).
        6. Stop lock renewal in the ``finally`` block.
        """
        logger.info(
            "Worker %s: executing job %s (type=%s, attempt=%d)",
            self.worker_id,
            job.id,
            job.job_type,
            job.attempts,
        )

        renewal_stop = asyncio.Event()

        async def _renew_lock_loop() -> None:
            """Periodically renew the job lock to prevent lease expiry."""
            while True:
                try:
                    await asyncio.wait_for(
                        renewal_stop.wait(),
                        timeout=self.lock_ttl / 3,
                    )
                except asyncio.TimeoutError:
                    pass  # time to renew
                if renewal_stop.is_set():
                    break
                async with AsyncSessionLocal() as renew_db:
                    renewed = await JobService(renew_db).renew_lock(
                        job.id, self.worker_id, self.lock_ttl
                    )
                    if not renewed:
                        logger.warning(
                            "Worker %s: lost lock on job %s — "
                            "another worker may reclaim it",
                            self.worker_id,
                            job.id,
                        )
                        break

        renewal_task = asyncio.create_task(_renew_lock_loop())

        try:
            # ── Instantiate handler ──────────────────────────────
            handler: JobHandler | None = get_handler(job.job_type)
            if handler is None:
                raise ValueError(
                    f"No handler registered for job type: {job.job_type}"
                )

            # ── Execute in its own session ───────────────────────
            async with AsyncSessionLocal() as db:
                service = JobService(db)
                result_data = await handler.execute(db, job)
                await service.complete_job(job.id, result_data)

            logger.info(
                "Worker %s: job %s completed",
                self.worker_id,
                job.id,
            )

        except Exception as exc:
            logger.exception(
                "Worker %s: job %s failed — %s",
                self.worker_id,
                job.id,
                exc,
            )
            async with AsyncSessionLocal() as db:
                service = JobService(db)
                await service.fail_job(job.id, str(exc))

        finally:
            renewal_stop.set()
            renewal_task.cancel()
            try:
                await renewal_task
            except (asyncio.CancelledError, Exception):
                pass
