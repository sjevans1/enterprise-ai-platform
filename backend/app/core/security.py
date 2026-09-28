"""Security utilities: password hashing, JWT, encryption, secrets management."""

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from app.core.config import settings

# ── Password / token hashing ───────────────────────────────────
# bcrypt has a 72-byte input limit. For tokens (JWTs, session tokens)
# that exceed this, we pre-hash with SHA-256 (base64 → 44 bytes, safe).
def _bcrypt_hash(value: str) -> str:
    raw = value.encode("utf-8")
    if len(raw) > 72:
        raw = base64.b64encode(hashlib.sha256(raw).digest())
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode("utf-8")


def hash_password(password: str) -> str:
    """Hash a password or token using bcrypt.

    Handles inputs longer than bcrypt's 72-byte limit by SHA-256 pre-hashing.
    """
    return _bcrypt_hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password or token against its bcrypt hash."""
    try:
        raw = plain.encode("utf-8")
        if len(raw) > 72:
            raw = base64.b64encode(hashlib.sha256(raw).digest())
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ── JWT tokens ────────────────────────────────────────────────
def _get_jwt_key() -> str:
    return settings.jwt_secret_key.get_secret_value()


def create_access_token(
    subject: str | int,
    *,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
    **kwargs: Any,
) -> str:
    """Create a JWT access token.

    Args:
        subject: User id or email (stored as 'sub' claim).
        expires_delta: Token expiry. Defaults to jwt_expire_minutes.
        extra_claims: Additional claims to include.

    Returns:
        Encoded JWT string.
    """
    to_encode: dict[str, Any] = {"sub": str(subject)}

    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.jwt_expire_minutes)

    expire = datetime.now(timezone.utc) + expires_delta
    to_encode["exp"] = expire
    to_encode["iat"] = datetime.now(timezone.utc)
    to_encode["jti"] = secrets.token_urlsafe(16)

    if extra_claims:
        to_encode.update(extra_claims)

    return jwt.encode(to_encode, _get_jwt_key(), algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str | int) -> str:
    """Create a longer-lived refresh token."""
    return create_access_token(
        subject,
        expires_delta=timedelta(minutes=settings.jwt_refresh_expire_minutes),
        token_type="refresh",
    )


def verify_token(token: str) -> dict[str, Any] | None:
    """Verify a JWT and return its claims, or None if invalid."""
    try:
        payload = jwt.decode(token, _get_jwt_key(), algorithms=[settings.jwt_algorithm])
        return payload
    except (JWTError, InvalidToken):
        return None


def create_bootstrap_token() -> str:
    """Generate a cryptographically random one-time bootstrap token."""
    return secrets.token_urlsafe(32)


def verify_bootstrap_token(token: str) -> bool:
    """Verify a bootstrap token against the configured value."""
    expected = settings.bootstrap_token.get_secret_value()
    return secrets.compare_digest(token, expected)


# ── Fernet encryption for secrets at rest ─────────────────────
def _get_fernet() -> Fernet:
    key = settings.master_key.get_secret_value()
    # master_key must be a URL-safe base64-encoded 32-byte key
    fernet_key = base64.urlsafe_b64encode(key.encode().ljust(32, b"\0")[:32])
    return Fernet(fernet_key)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret string for storage at rest."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a secret string stored at rest."""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()


# ── Session management helpers ────────────────────────────────
def generate_session_token() -> str:
    """Generate a secure random session token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash a session token for storage (SHA-256)."""
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def generate_api_key() -> str:
    """Generate a random API key."""
    return secrets.token_urlsafe(32)
