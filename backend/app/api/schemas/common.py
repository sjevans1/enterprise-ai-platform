"""Common schemas shared across API endpoints."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now())


class SuccessResponse(BaseModel):
    success: bool = True
    message: str | None = None
    data: dict[str, Any] | list[Any] | None = None


class Pagination(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
    total: int | None = None


class PaginatedResponse(BaseModel):
    items: list[Any]
    pagination: Pagination


class ProcessingLocation(str, Enum):
    LOCAL = "LOCAL"
    REMOTE = "REMOTE"
    UNKNOWN = "UNKNOWN"


# ── Chat schemas ─────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str  # user, assistant, tool, system
    content: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    provider: str | None = None
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    conversation_id: str | None = None


class Citation(BaseModel):
    source_type: str  # document, sql, tool
    source_id: str
    title: str | None = None
    passage: str | None = None
    citation: str | None = None
    confidence: float | None = None


class AnswerRecord(BaseModel):
    final_answer: str | None = None
    completion_status: str
    evidence: list[Citation] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    truncation_info: str | None = None
    processing_location: str | None = None
    model_identity: str | None = None
    as_of_time: datetime | None = None
    execution_timestamp: datetime = Field(default_factory=lambda: datetime.now())


class ChatResponse(BaseModel):
    response_type: str = "content"  # content, tool_use, error, done
    message: ChatMessage | None = None
    delta: str | None = None  # for streaming
    answer: AnswerRecord | None = None
    finish_reason: str | None = None


# ── DataSource schemas ───────────────────────────────────────

class DataSourceConfig(BaseModel):
    """Configuration for a data source connection."""
    name: str
    connector_type: str
    display_name: str | None = None
    description: str | None = None
    connection_params: dict[str, Any]  # host, port, database, username, etc.
    ssl_mode: str | None = None
    is_test: bool = False


class DataSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    connector_type: str
    display_name: str | None
    description: str | None
    dialect: str | None
    is_active: bool
    is_approved: bool
    is_test: bool
    last_tested_at: datetime | None


# ── Document schemas ─────────────────────────────────────────

class DocumentUploadResponse(BaseModel):
    document_id: str
    filename: str
    file_size: int
    mime_type: str
    status: str
    job_id: str | None = None


# ── Report schemas ───────────────────────────────────────────

class ReportRequest(BaseModel):
    name: str
    description: str | None = None
    query_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    format: str = "markdown"  # markdown, html, pdf


class ReportResponse(BaseModel):
    report_id: str
    name: str
    status: str
    format: str
    download_url: str | None = None


# ── Audit schemas ────────────────────────────────────────────

class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    event_type: str
    user_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    outcome: str | None = None
    created_at: datetime
