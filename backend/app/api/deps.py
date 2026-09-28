"""FastAPI dependencies for authentication, authorization, and services.

Security note: The browser calls the application API. It must not receive
provider keys, database credentials, or unrestricted access to Hermes/Ollama.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import auth_service
from app.core.config import settings
from app.core.permissions import Permission
from app.core.security import verify_token
from app.db.connection import get_db
from app.db.models import Role, SessionToken, User, UserRole

logger = logging.getLogger(__name__)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and verify the current user from the Authorization header.

    Uses bearer JWT tokens. Does NOT accept provider keys or database credentials.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header[7:]

    # Verify JWT
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Fetch user from DB
    stmt = select(User).where(User.id == user_id, User.status == "active")
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user or user.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Dependency that returns the current active user."""
    if current_user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )
    return current_user


def require_permission(permission_code: str):
    """Dependency factory: requires a specific permission.

    Usage:
        @app.get("/users")
        async def list_users(user: User = Depends(require_permission(Permission.USER_READ))):
            ...
    """
    async def _permission_check(
        current_user: Annotated[User, Depends(get_current_user)],
        db: AsyncSession = Depends(get_db),
    ) -> User:
        has_perm = await auth_service.has_permission(db, current_user, permission_code)
        if not has_perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission_code}' is required",
            )
        return current_user

    return _permission_check


# Common dependency annotations
CurrentUser = Annotated[User, Depends(get_current_active_user)]
