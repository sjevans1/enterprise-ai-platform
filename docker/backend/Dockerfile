# ─────────────────────────────────────────────────────────────────────
# Backend API Dockerfile
# Multi-stage build with CPU-only PyTorch and sentence-transformers
# ─────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

# Install system dependencies required by psycopg, lxml, and Pillow
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libxml2-dev \
        libxslt1-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

# ── Dependencies stage ───────────────────────────────────────────────
FROM base AS dependencies

# Install CPU-only PyTorch first (largest layer, best cache invalidation)
RUN pip install --no-cache-dir torch==2.14.0+cpu --index-url https://download.pytorch.org/whl/cpu

# Install Python dependencies from pyproject.toml
COPY backend/pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir "uvicorn[standard]>=0.29" \
    && pip install --no-cache-dir "fastapi>=0.110" \
    && pip install --no-cache-dir \
        "sqlalchemy>=2.0" \
        "alembic>=1.13" \
        "psycopg[binary]>=3.1" \
        "pgvector>=0.3.0" \
        "psycopg-pool>=3.1" \
        "pydantic>=2.0" \
        "python-jose[cryptography]>=3.3" \
        "passlib[bcrypt]>=2.1" \
        "python-multipart>=0.0.0" \
        "pyjwt>=2.8" \
        "sentence-transformers>=2.2" \
        "numpy>=1.24" \
        "openai>=1.0" \
        "PyPDF2>=3.0" \
        "python-docx>=0.8" \
        "openpyxl>=3.1" \
        "beautifulsoup4>=4.12" \
        "lxml>=5.0" \
        "chardet>=5.0"

# ── Application stage ────────────────────────────────────────────────
FROM base AS application

WORKDIR /app

# Copy installed packages from dependencies stage
COPY --from=dependencies /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=dependencies /usr/local/bin /usr/local/bin

# Copy application code
COPY backend/app/ ./app/
COPY backend/init_db.py ./init_db.py

# Create data directories
RUN mkdir -p .data/uploads .cache/embeddings

# Switch to non-root user
USER app

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5)" || exit 1

# Default: run the API server
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
