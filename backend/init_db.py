"""Initialize the database: create tables and seed default data."""
import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app.db.base import Base
from app.db.connection import engine, AsyncSessionLocal
from app.db.models import *  # noqa: F401
from app.core.config import settings


async def init_database():
    """Create all tables and seed initial data."""
    # Drop all existing tables (clean slate for development)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    print("Existing tables dropped")

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("All tables created")

    # Seed permissions and roles
    async with AsyncSessionLocal() as db:
        from app.auth.service import auth_service
        await auth_service.seed_permissions_and_roles(db)
    print("Permissions and roles seeded")

    print(f"\n  Database: postgresql+psycopg://app_admin@127.0.0.1:{settings.postgres_port}/{settings.postgres_db}")
    print(f"  App env: {settings.app_env}")
    print(f"  Default provider: {settings.default_provider}")
    print(f"  Remote model allowed: {settings.remote_model_allowed}")
    print("\nDatabase initialization complete.")


if __name__ == "__main__":
    asyncio.run(init_database())
