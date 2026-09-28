"""Pydantic schemas for the job queue API endpoints."""
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobType(str, Enum):
    """Supported background job types."""

    DOCUMENT_INGEST = "document_ingest"
    REPORT_GENERATION = "report_generation"


class JobStatus(str, Enum):
    """Job lifecycle statuses (mirrors the ORM enum in app.db.models)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobSubmitRequest(BaseModel):
    """Request body for submitting a new background job."""

    job_type: JobType = Field(
        ..., description="Type of job to enqueue"
    )
    payload: dict[str, Any] = Field(
        default_factory=dict, description="Job parameters as a JSON object"
    )
    priority: int = Field(
        default=0, ge=0, le=100, description="Priority 0-100; higher runs first"
    )
    max_retries: int | None = Field(
        default=None, ge=0, le=10, description="Override default max retries"
    )
    expires_at: datetime | None = Field(
        default=None, description="Optional expiration timestamp (UTC)"
    )


class JobSubmitResponse(BaseModel):
    """Response from submitting a job."""

    job_id: str
    job_type: str
    status: JobStatus
    submitted_at: datetime
    message: str = "Job submitted successfully"


class JobStatusResponse(BaseModel):
    """Response for a job status query."""

    job_id: str
    job_type: str
    status: JobStatus
    payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    priority: int
    attempts: int
    max_retries: int
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    worker_id: str | None = None

    @classmethod
    def from_orm_job(cls, job: Any) -> "JobStatusResponse":
        """Build a JobStatusResponse from a JobQueue ORM instance.

        Handles JSON-string → dict conversion for *payload* and *result*,
        and normalises the status enum to the API schema enum.
        """

        @staticmethod
        def _parse_json(value: str | None) -> dict[str, Any] | None:
            if value is None:
                return None
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except (json.JSONDecodeError, TypeError):
                return {"raw": value}

        return cls(
            job_id=job.id,
            job_type=job.job_type,
            status=JobStatus[job.status.name] if hasattr(job.status, 'name') else JobStatus(job.status),
            payload=_parse_json(job.payload),
            result=_parse_json(job.result),
            priority=job.priority,
            attempts=job.attempts,
            max_retries=job.max_retries,
            error_message=job.error_message,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            expires_at=job.expires_at,
            worker_id=job.worker_id,
        )
