"""Authentication and authorization service.

Leverages app.core.security.py for password hashing, JWT, and encryption.
Uses app.db.models ORM models and app.core.permissions for permission enums.
"""
from __future__ import annotations

import logging
import secrets as pysecrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.permissions import Permission, RoleName, BUILTIN_ROLE_PERMISSIONS
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
    verify_token,
    create_bootstrap_token as _gen_bootstrap_token,
    verify_bootstrap_token as _verify_bootstrap_token,
)
from app.db.models import (
    BootstrapToken,
    Role,
    RolePermission,
    SessionToken,
    User,
    UserRole,
    Permission as PermissionModel,
)
from app.db.base import Base
from app.core.audit import AuditEvent, AuditDetail  # noqa: F401 - ensure models load

logger = logging.getLogger(__name__)


class AuthService:
    """Handles authentication, session management, and authorization."""

    # ── Bootstrap ──────────────────────────────────────────────────

    async def is_bootstrap_required(self, db: AsyncSession) -> bool:
        """Check if any admin user exists."""
        from sqlalchemy import select as sa_select
        stmt = (
            sa_select(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.name == RoleName.ADMIN.value, User.status == "active")
        )
        result = await db.execute(stmt)
        admin = result.scalars().first()
        return admin is None

    async def generate_bootstrap_token(self, db: AsyncSession) -> str:
        """Generate a one-time bootstrap token."""
        if not await self.is_bootstrap_required(db):
            raise ValueError("Bootstrap already completed")

        token = pysecrets.token_urlsafe(32)
        token_hash = hash_password(token)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=2)

        bt = BootstrapToken(
            token_hash=token_hash,
            expires_at=expires_at,
            used_at=None,
            used_by=None,
        )
        db.add(bt)
        await db.commit()
        return token

    async def verify_bootstrap_and_create_admin(
        self,
        db: AsyncSession,
        token: str,
        email: str,
        password: str,
        full_name: str | None = None,
    ) -> User:
        """Verify bootstrap token and create the first admin user.

        This method verifies the token against both the configured
        BOOTSTRAP_TOKEN and dynamically generated DB tokens.
        """
        if not await self.is_bootstrap_required(db):
            raise ValueError("Bootstrap already completed")

        # Check configured token
        configured = settings.bootstrap_token.get_secret_value()
        if configured:
            import secrets as sec
            if sec.compare_digest(token, configured):
                return await self._create_admin_user(db, email, password, full_name)

        # Check dynamic tokens in DB
        stmt = (
            select(BootstrapToken)
            .where(
                BootstrapToken.used_at.is_(None),
                BootstrapToken.expires_at > datetime.now(timezone.utc),
            )
        )
        result = await db.execute(stmt)
        tokens = result.scalars().all()
        for bt in tokens:
            if verify_password(token, bt.token_hash):
                # Mark as used (single-use token)
                bt.used_at = datetime.now(timezone.utc)
                user = await self._create_admin_user(db, email, password, full_name)
                await db.commit()
                return user

        raise ValueError("Invalid or expired bootstrap token")

    def validate_password(self, password: str) -> tuple[bool, str | None]:
        """Validate password strength.

        Returns (True, None) if valid, (False, reason) otherwise.
        """
        import re
        if len(password) < 8:
            return False, "Password must be at least 8 characters"
        if not re.search(r"[A-Z]", password):
            return False, "Password must contain at least one uppercase letter"
        if not re.search(r"[a-z]", password):
            return False, "Password must contain at least one lowercase letter"
        if not re.search(r"\d", password):
            return False, "Password must contain at least one digit"
        if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?`~]", password):
            return False, "Password must contain at least one special character"
        return True, None

    async def _create_admin_user(
        self,
        db: AsyncSession,
        email: str,
        password: str,
        full_name: str | None = None,
    ) -> User:
        """Create the first admin user (token already verified by caller)."""
        valid, msg = self.validate_password(password)
        if not valid:
            raise ValueError(msg)

        # Check email uniqueness
        result = await db.execute(select(User).where(User.email == email))
        if result.scalars().first():
            raise ValueError("Email already registered")

        user_id = str(uuid.uuid4())
        hashed = hash_password(password)
        user = User(
            id=user_id,
            email=email,
            password_hash=hashed,
            full_name=full_name or email,
            status="active",
            is_bootstrap=True,
        )
        db.add(user)

        # Assign admin role
        stmt = select(Role).where(Role.name == RoleName.ADMIN.value)
        result = await db.execute(stmt)
        admin_role = result.scalars().first()
        if admin_role:
            ur = UserRole(user_id=user_id, role_id=admin_role.id)
            db.add(ur)

        await db.commit()
        await db.refresh(user)
        logger.info(f"First admin created via bootstrap: {email}")
        return user

    # ── Authentication ─────────────────────────────────────────────

    async def authenticate(self, db: AsyncSession, email: str, password: str) -> User | None:
        """Authenticate a user by email and password."""
        stmt = select(User).where(User.email == email, User.status == "active")
        result = await db.execute(stmt)
        user = result.scalars().first()
        if not user or user.deleted_at is not None:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    # ── Token / Session ────────────────────────────────────────────

    async def create_session(
        self,
        db: AsyncSession,
        user: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[SessionToken, str]:
        """Create a server-side session record and issue a JWT access token."""
        access_token = create_access_token(user.id, extra_claims={"token_type": "access"})
        refresh_token = create_refresh_token(user.id)

        session_id = str(uuid.uuid4())
        session = SessionToken(
            id=session_id,
            user_id=user.id,
            token_hash=hash_password(access_token),
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes),
            last_used_at=datetime.now(timezone.utc),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session, access_token

    async def revoke_session(self, db: AsyncSession, session_id: str) -> bool:
        """Revoke a session (logout)."""
        stmt = select(SessionToken).where(SessionToken.id == session_id)
        result = await db.execute(stmt)
        session = result.scalars().first()
        if session and session.revoked_at is None:
            session.revoked_at = datetime.now(timezone.utc)
            await db.commit()
            return True
        return False

    async def cleanup_expired_sessions(self, db: AsyncSession):
        """Remove expired sessions."""
        await db.execute(
            delete(SessionToken).where(
                SessionToken.expires_at < datetime.now(timezone.utc)
            )
        )
        await db.commit()

    # ── Authorization ─────────────────────────────────────────────

    async def get_user_permission_codes(self, db: AsyncSession, user: User) -> set[str]:
        """Resolve all effective permission codes for a user.

        Combines role-based permissions and explicit per-user grants.
        Administrators get all permissions.
        """
        # Role-based
        stmt = (
            select(PermissionModel.name)
            .join(RolePermission, PermissionModel.id == RolePermission.permission_id)
            .join(UserRole, RolePermission.role_id == UserRole.role_id)
            .where(UserRole.user_id == user.id)
        )
        result = await db.execute(stmt)
        role_perms = {row[0] for row in result.all()}

        # Check if user is admin (gets all permissions)
        stmt2 = (
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == user.id,
                Role.name == RoleName.ADMIN.value,
            )
        )
        result2 = await db.execute(stmt2)
        if result2.scalars().first():
            all_perms = {p.value for p in Permission}
            return role_perms | all_perms

        return role_perms

    async def has_permission(self, db: AsyncSession, user: User, permission_code: str) -> bool:
        """Check if a user has a specific permission code."""
        perms = await self.get_user_permission_codes(db, user)
        return permission_code in perms

    async def seed_permissions_and_roles(self, db: AsyncSession):
        """Seed the permissions and roles tables with built-in data."""
        # Check if already seeded
        stmt = select(Role).where(Role.name == RoleName.ADMIN.value)
        result = await db.execute(stmt)
        if result.scalars().first():
            return

        # Create permission records
        perm_map: dict[str, PermissionModel] = {}
        for perm in Permission:
            perm_obj = PermissionModel(
                id=str(uuid.uuid4()),
                name=perm.value,
                description=f"{perm.value} permission",
                scope=perm.value.split(":")[0] if ":" in perm.value else perm.value,
            )
            db.add(perm_obj)
            perm_map[perm.value] = perm_obj

        await db.flush()

        # Create roles
        role_defs = {
            RoleName.ADMIN.value: "Application administrator with full access",
            RoleName.STANDARD_USER.value: "Standard authenticated user",
            RoleName.AUDITOR.value: "Auditor with read-only audit access",
        }

        for role_name, role_desc in role_defs.items():
            role = Role(
                id=str(uuid.uuid4()),
                name=role_name,
                description=role_desc,
                is_system=True,
                is_active=True,
            )
            db.add(role)

            # Assign permissions
            perms_for_role = BUILTIN_ROLE_PERMISSIONS.get(
                RoleName(role_name), []
            )
            for perm in perms_for_role:
                rp = RolePermission(
                    role_id=role.id,
                    permission_id=perm_map[perm.value].id,
                    granted_by=None,
                )
                db.add(rp)

        await db.commit()
        logger.info("Permissions and roles seeded")


auth_service = AuthService()
