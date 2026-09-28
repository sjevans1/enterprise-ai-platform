"""First-run bootstrap: create the initial administrator.

Protected flow: a one-time bootstrap token is required.
After the first admin is created, bootstrap is disabled.
No fixed default credentials are shipped.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.auth import BootstrapRequest
from app.api.schemas.common import SuccessResponse
from app.auth.service import auth_service
from app.core.audit import AuditEventType, audit_context
from app.core.config import settings
from app.db.connection import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["bootstrap"])


@router.get("/bootstrap/status")
async def bootstrap_status(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Check if bootstrap is required and whether a token exists."""
    required = await auth_service.is_bootstrap_required(db)
    return {
        "bootstrap_required": required,
        "bootstrap_token_configured": bool(settings.bootstrap_token.get_secret_value()),
    }


@router.post("/bootstrap/token")
async def request_bootstrap_token(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Generate a one-time bootstrap token for initial admin creation.

    In development, a token is generated and returned.
    In production, the operator sets BOOTSTRAP_TOKEN in the environment.
    """
    required = await auth_service.is_bootstrap_required(db)
    if not required:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bootstrap already completed",
        )

    # Use configured bootstrap token from environment if set
    configured = settings.bootstrap_token.get_secret_value()
    if configured:
        return {
            "message": "Bootstrap token is configured in environment",
            "expires_in_minutes": 120,
        }

    # Development fallback: generate a dynamic token
    token = await auth_service.generate_bootstrap_token(db)
    return {
        "message": "Bootstrap token generated (development mode)",
        "bootstrap_token": token,
        "expires_in_minutes": 120,
    }


@router.post("/bootstrap/admin")
async def create_first_admin(
    request: BootstrapRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create the first admin user using a bootstrap token."""
    # Check if already bootstrapped
    required = await auth_service.is_bootstrap_required(db)
    if not required:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bootstrap already completed",
        )

    # Verify token and create admin in one call.
    # This checks both the configured BOOTSTRAP_TOKEN env var
    # and dynamically generated DB tokens.
    try:
        user = await auth_service.verify_bootstrap_and_create_admin(
            db,
            request.token,
            request.email,
            request.password,
            request.full_name,
        )
    except ValueError as e:
        async with audit_context(db) as audit:
            await audit.log(
                AuditEventType.AUTH_BOOTSTRAP,
                outcome="failure",
                details={"reason": str(e), "email": request.email},
            )
        status_code = 401 if "token" in str(e).lower() else 400
        raise HTTPException(
            status_code=status_code,
            detail=str(e),
        )

    async with audit_context(db) as audit:
        await audit.log(
            AuditEventType.AUTH_BOOTSTRAP,
            user_id=user.id,
            outcome="success",
            details={"email": request.email},
        )

    return {
        "message": "Administrator account created",
        "user_id": user.id,
        "email": user.email,
    }
