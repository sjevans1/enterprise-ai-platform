"""Audit service: immutable event logging for security-sensitive actions."""

import json
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any, AsyncContextManager
from uuid import uuid4

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import DateTime, String, Boolean, Text
from datetime import datetime, timezone


class AuditEventType(str, Enum):
    """Types of auditable events."""

    AUTH_LOGIN = "auth.login"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_LOGOUT = "auth.logout"
    AUTH_BOOTSTRAP = "auth.bootstrap"

    CONFIG_CHANGE = "config.change"
    CONFIG_PROVIDER = "config.provider"
    CONFIG_DATASOURCE = "config.datasource"
    CONFIG_CATALOG = "config.catalog"

    PERMISSION_CHANGE = "permission.change"

    CHAT_START = "chat.start"
    CHAT_MESSAGE = "chat.message"
    CHAT_FEEDBACK = "chat.feedback"

    INGESTION_START = "ingestion.start"
    INGESTION_COMPLETE = "ingestion.complete"
    INGESTION_FAIL = "ingestion.fail"
    DOCUMENT_DELETE = "document.delete"

    QUERY_EXECUTE = "query.execute"
    QUERY_REJECT = "query.reject"

    RETRIEVAL = "retrieval"
    PROVIDER_REQUEST = "provider.request"

    TOOL_EXECUTE = "tool.execute"
    REPORT_GENERATE = "report.generate"
    EXPORT = "export"

    APPROVAL_REQUEST = "approval.request"
    APPROVAL_GRANT = "approval.grant"
    APPROVAL_DENY = "approval.deny"


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    target_type: Mapped[str | None] = mapped_column(String, nullable=True)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    processing_location: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )

    def set_details(self, details: dict[str, Any]) -> None:
        """Store details in the event (for internal use, redacted)."""
        self._details = details  # type: ignore[attr-defined]

    @property
    def details(self) -> dict[str, Any]:
        return getattr(self, "_details", {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "provider": self.provider,
            "model": self.model,
            "processing_location": self.processing_location,
            "outcome": self.outcome,
            "ip_address": self.ip_address,
            "created_at": self.created_at.isoformat(),
        }


class AuditService:
    """Records audit events to the database. Append-only by design."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def log(
        self,
        event_type: AuditEventType,
        *,
        user_id: str | None = None,
        conversation_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        processing_location: str | None = None,
        outcome: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        """Record an audit event. Returns the event ID."""
        from datetime import timezone
        event_id = str(uuid4())
        stmt = insert(AuditEvent).values(
            id=event_id,
            event_type=event_type.value,
            user_id=user_id,
            conversation_id=conversation_id,
            target_type=target_type,
            target_id=target_id,
            provider=provider,
            model=model,
            processing_location=processing_location,
            outcome=outcome,
            ip_address=ip_address,
            user_agent=user_agent,
            created_at=datetime.now(timezone.utc),
        )
        await self._db.execute(stmt)
        await self._db.flush()

        # Store details in a separate JSON column for security
        if details:
            await self._db.execute(
                insert(AuditDetail).values(
                    event_id=event_id,
                    key="details",
                    value_json=json.dumps(details, default=str),
                )
            )
        return event_id

    async def get_events(
        self,
        *,
        event_types: list[AuditEventType] | None = None,
        user_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
        if event_types:
            types = [e.value if isinstance(e, AuditEventType) else e for e in event_types]
            stmt = stmt.where(AuditEvent.event_type.in_(types))
        if user_id:
            stmt = stmt.where(AuditEvent.user_id == user_id)
        if since:
            stmt = stmt.where(AuditEvent.created_at >= since)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())


class AuditDetail(Base):
    __tablename__ = "audit_details"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    event_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    key: Mapped[str] = mapped_column(String, nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


@asynccontextmanager
async def audit_context(db: AsyncSession) -> AsyncContextManager[AuditService]:
    """Context manager for getting an AuditService within a DB session."""
    yield AuditService(db)
