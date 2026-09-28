"""CLI entry point for the background job worker.

Run from the backend directory using the project venv::

    .venv/bin/python -m app.jobs.runner
    .venv/bin/python -m app.jobs.runner --poll-interval 2 --lock-ttl 600 --worker-id my-worker

The worker connects to PostgreSQL directly and processes jobs from the
``job_queue`` table.  No external queue system (Redis, etc.) is required.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.jobs.worker import JobWorker

logger = logging.getLogger("job-worker")


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="app.jobs.runner",
        description="Enterprise AI Platform — PostgreSQL-backed job worker",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Seconds between poll cycles when no jobs are available (default: 5)",
    )
    parser.add_argument(
        "--lock-ttl",
        type=int,
        default=300,
        help="Lock lease duration in seconds; expired locks are reclaimable (default: 300)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=1,
        help="Maximum number of jobs to process in parallel (default: 1)",
    )
    parser.add_argument(
        "--worker-id",
        type=str,
        default=None,
        help="Custom worker ID (default: auto-generated)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug-level logging",
    )
    args = parser.parse_args()

    _setup_logging(args.verbose)

    worker = JobWorker(
        worker_id=args.worker_id,
        poll_interval=args.poll_interval,
        lock_ttl=args.lock_ttl,
        max_concurrent=args.max_concurrent,
    )

    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
