"""Health check and service status endpoint."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_db
from app.db.models import User

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Basic health check. Database liveness probe."""
    try:
        await db.execute(select(User).limit(1))
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {type(e).__name__}"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "database": db_status,
        "providers": {
            "hermes": {"configured": True},
            "ollama": {"configured": True},
            "openai_compatible": {"configured": True},
        },
    }


@router.get("/version")
async def version_info() -> dict[str, Any]:
    return {
        "name": "Enterprise AI Platform",
        "version": "1.0.0-milestone1",
        "milestone": "Milestone 1 - Foundation and Model Gateway",
    }
