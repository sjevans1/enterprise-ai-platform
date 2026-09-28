"""Main FastAPI application entry point.

Milestone 1: Foundation and Model Gateway.

This module wires up:
- Database lifecycle (init/close on startup/shutdown)
- CORS and security headers
- Authentication routes (bootstrap, login, refresh, logout)
- First-run bootstrap flow
- Chat routes with provider abstraction
- Basic health check

The application uses a modular monolith design: clear service interfaces
without splitting every component into a network service.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.api.routes import (
    auth, bootstrap, chat, documents, health, jobs, structured_data,
)
from app.core.config import settings
from app.core.audit import AuditService
from app.db.connection import AsyncSessionLocal, close_db, engine, init_db
from app.db.models import BootstrapToken, Role, Permission, RolePermission, User, UserRole

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: initialize services on startup, clean up on shutdown."""
    # Database
    await init_db()
    logger.info("Database initialized")

    # Seed system roles and permissions if not present
    await _seed_default_data()
    logger.info("Default data seeded")

    yield

    # Cleanup
    await close_db()
    logger.info("Application shutting down")


async def _seed_default_data():
    """Seed system roles and permissions if not present.

    Uses auth_service.seed_permissions_and_roles which maps the built-in
    Permission enum and RoleName enum to database records.
    """
    async with AsyncSessionLocal() as session:
        await auth_service.seed_permissions_and_roles(session)
    logger.info("Default data seeded")


# Import auth_service here to avoid circular import at module load time
from app.auth.service import auth_service


app = FastAPI(
    title=settings.app_name,
    description="Portable Enterprise AI & RAG Platform MVP",
    version="1.0.0-milestone1",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Processing-Location"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# Register routers FIRST — before the static file mount.
# StaticFiles mounted at "/" would otherwise shadow /api/v1/* routes
# when the frontend/dist directory exists (it catches 404s and serves
# index.html as fallback, intercepting requests meant for the API).
app.include_router(health.router, prefix="/api/v1")
app.include_router(bootstrap.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(structured_data.router, prefix="/api/v1")

# Mount static files for frontend (if built) — AFTER routers so API routes win
import os
static_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend", "dist")
if os.path.isdir(static_path):
    app.mount("/", StaticFiles(directory=static_path, html=True), name="frontend")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler — never leak internal details."""
    logger.error(f"Unhandled exception on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "code": "internal_error"},
    )
