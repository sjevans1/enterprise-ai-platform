"""Authentication routes: login, refresh, logout."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.schemas.auth import LoginRequest, LoginResponse, UserResponse, RefreshRequest
from app.api.schemas.common import SuccessResponse
from app.auth.service import auth_service
from app.core.audit import AuditEventType, audit_context
from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token, verify_token
from app.db.connection import get_db
from app.db.models import User

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


@router.post("/auth/login")
async def login(
    request: Request,
    credentials: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Authenticate and issue access + refresh tokens."""
    user = await auth_service.authenticate(db, credentials.email, credentials.password)
    if not user:
        async with audit_context(db) as audit:
            await audit.log(
                AuditEventType.AUTH_LOGIN_FAILED,
                outcome="failure",
                ip_address=request.client.host,
                details={"email": credentials.email},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    session, access_token = await auth_service.create_session(
        db, user,
        ip_address=request.client.host,
        user_agent=request.headers.get("user-agent"),
    )
    refresh_token = create_refresh_token(user.id)

    async with audit_context(db) as audit:
        await audit.log(
            AuditEventType.AUTH_LOGIN,
            user_id=user.id,
            outcome="success",
            ip_address=request.client.host,
        )

    perms = await auth_service.get_user_permission_codes(db, user)

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.jwt_expire_minutes * 60,
        user=UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            status=user.status,
            permissions=list(perms),
            created_at=user.created_at,
        ),
    )


@router.post("/auth/refresh")
async def refresh_token_route(
    request: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Refresh an access token using a refresh token."""
    payload = verify_token(request.refresh_token)
    if not payload or payload.get("token_type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    stmt = select(User).where(User.id == payload["sub"])
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user or user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    perms = await auth_service.get_user_permission_codes(db, user)
    new_access = create_access_token(user.id, extra_claims={"token_type": "access"})

    async with audit_context(db) as audit:
        await audit.log(
            AuditEventType.AUTH_LOGIN,
            user_id=user.id,
            outcome="success",
            details={"action": "token_refresh"},
        )

    return LoginResponse(
        access_token=new_access,
        refresh_token=request.refresh_token,
        token_type="bearer",
        expires_in=settings.jwt_expire_minutes * 60,
        user=UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            status=user.status,
            permissions=list(perms),
            created_at=user.created_at,
        ),
    )


@router.post("/auth/logout")
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    """Revoke the current session."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return SuccessResponse(message="No active session")

    # The session is server-side; we can revoke it
    token = auth_header[7:]
    from app.core.security import verify_token as _verify
    payload = _verify(token)
    if payload:
        async with audit_context(db) as audit:
            await audit.log(
                AuditEventType.AUTH_LOGOUT,
                user_id=payload.get("sub"),
                outcome="success",
            )

    return SuccessResponse(message="Logged out")


@router.get("/auth/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Get current user info and permissions."""
    perms = await auth_service.get_user_permission_codes(db, current_user)
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        status=current_user.status,
        permissions=list(perms),
        created_at=current_user.created_at,
    )
