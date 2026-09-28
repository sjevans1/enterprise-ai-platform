# BUILD_STATUS.md

> **Enterprise AI & RAG Platform — MVP Build**  
> Started: 2026-09-27  
> Last updated: 2026-09-27  
> Lead: Hermes Agent v0.21.5

---

## Environment

| Item | Value |
|---|---|
| OS | Ubuntu 24.04.4 LTS (Noble Numbat) |
| Kernel | 6.6.87 |
| Arch | x86_64 |
| CPU | 16 cores |
| RAM | 15 Gi (13 Gi available) |
| Disk | 1 TB (945 Gi free) |
| Python | 3.14.7 (Hermes toolchain) / 3.12.3 (venv) |
| Node | v22.23.2 |
| pnpm | 11.21.0 |
| uv | 0.12.0 |
| Docker | 29.1.3 (static binary at `~/.local/bin/docker-static/`) |
| Docker Compose | v5.5.1 (standalone binary) |
| rootlesskit | 2.3.4 |
| PostgreSQL | 17.11 (via micromamba, port 5433) |
| pgvector | 0.8.1 |
| Hermes | v0.21.5 |
| Ollama | v0.34.4 (downloading) |
| GPU | None — CPU-only operation required |

### Known environment limitations

1. **No root access** — Docker daemon cannot run in standard mode. Rootless Docker setup is in progress (rootlesskit available; slirp4netns pending). For development, services run directly (PostgreSQL via binary, API via uvicorn, frontend via Vite). Docker Compose files are created as deployment deliverables.
2. **No Docker daemon running** — Docker static binaries are extracted but not yet running as rootless daemon. This is an active blocker for `docker compose up -d` validation.
3. **PostgreSQL running on port 5433** (not 5432) to avoid conflicts.
4. **Hermes API server not yet enabled** — requires `API_SERVER_ENABLED=true` in config and a bearer key.
5. **Ollama download was interrupted** — re-downloading in background.
6. **No GPU available** — all model inference is CPU-only. Local embedding models must be CPU-compatible.

### Verified working tools

- PostgreSQL 17.11 with pgvector + pgcrypto + pg_trgm extensions
- Python 3.12 venv with FastAPI, SQLAlchemy, Pydantic, psycopg3, torch (CPU), sentence-transformers
- Docker Compose v5.5.1 binary
- rootlesskit 2.3.4

---

## Implementation Sequence

### Milestone 0 — Preflight and Design [IN PROGRESS]
- [x] Environment inspection
- [x] Tool versions identified
- [x] PostgreSQL 17.11 + pgvector running on port 5433
- [x] Python venv created with core dependencies
- [x] Hermes API server docs reviewed (v0.21.5)
- [x] Docker static binaries available
- [x] Project structure created
- [ ] Docker Compose deployment validated (BLOCKED: rootless Docker not configured)
- [ ] Hermes API server enabled and tested
- [ ] Ollama installed and model pulled
- [ ] Primary embedder model downloaded

### Milestone 1 — Foundation and Model Gateway [PENDING]
- [ ] Container startup / service orchestration
- [ ] Alembic migrations applied
- [ ] First-run bootstrap (admin creation)
- [ ] Login and session management
- [ ] Basic permission enforcement
- [ ] Audit event recording
- [ ] ModelProvider abstraction with Hermes/Ollama/OpenAI adapters
- [ ] Chat API with streaming
- [ ] Verified: protected setup, login, general question

### Milestone 2 — Structured Data [PENDING]
- [ ] PostgreSQL connector
- [ ] Semantic catalog (editable metadata)
- [ ] SQL AST inspection and validation (read-only)
- [ ] Bounded query execution
- [ ] Query evidence recording
- [ ] Verified: correct and denied queries with fixture data

### Milestone 3 — Knowledge [PENDING]
- [ ] Document ingestion pipeline (upload → validate → extract → chunk → embed → index)
- [ ] Durable job queue (PostgreSQL-backed)
- [ ] Local embedding model
- [ ] Permission-aware vector + keyword retrieval
- [ ] Document replacement/deletion behavior
- [ ] Verified: upload, index, grounded answer with citation

### Milestone 4 — Hybrid Experience [PENDING]
- [ ] Document + SQL evidence combination
- [ ] Deterministic aggregations/comparisons
- [ ] Report generation
- [ ] CSV export with injection protection
- [ ] Synthetic demo scenarios
- [ ] Verified: hybrid question with both sources

### Milestone 5 — Complete Required Coverage [PENDING]
- [ ] MySQL/MariaDB connector
- [ ] Microsoft SQL Server connector
- [ ] Remaining document formats (HTML, DOCX, XLSX, etc.)
- [ ] Provider switching (config-based, no code changes)
- [ ] Laya adapter (disabled by default)
- [ ] Verified: each connector and format

### Milestone 6 — Deployment and Final Verification [PENDING]
- [ ] Docker/Podman Compose validated
- [ ] Offline profile verified
- [ ] Backup/restore tested
- [ ] Manual test plan documented
- [ ] Acceptance report complete

---

## Decisions and Assumptions

1. **Modular monolith**: Single FastAPI application with clear internal module boundaries. No separate network services per logical component (per spec section 3).
2. **Background jobs**: PostgreSQL-backed job queue (no Redis per spec section 3).
3. **Embeddings**: `sentence-transformers` with `all-MiniLM-L6-v2` (384-dim, CPU-compatible) as the default local embedding model.
4. **Secrets**: Fernet symmetric encryption (cryptography library), master key from environment variable `APP_MASTER_KEY`.
5. **PostgreSQL**: Runs on port 5433 in development to avoid conflicts. Docker Compose maps to standard 5432.
6. **Docker deployment**: Docker static binaries available but rootless mode not yet configured. Compose files are deliverables; will attempt rootless Docker setup. As fallback, development runs services directly (no Docker required).
7. **Demo data**: Synthetic business data for "Eleni's Bakery" (Jamaican milk-kefir "Gold" theme) consistent with REBOOT project branding.
8. **Model gateway**: Abstract `ModelProvider` interface; concrete implementations for Hermes, Ollama, and generic OpenAI-compatible providers.
9. **SQL validation**: AST-based inspection using SQLAlchemy's SQL parser, not regex. Read-only enforcement at multiple layers.
10. **Frontend**: React + TypeScript + Vite + Tailwind CSS. Mobile parity enforced (same component tree, not reflowed layout).

---

## Commands Log

```bash
# PostgreSQL (micromamba-managed)
/home/sjeva/.local/share/mamba/envs/postgres/bin/pg_ctl -D /home/sjeva/enterprise-ai-platform/infra/pgdata -l /home/sjeva/enterprise-ai-platform/infra/pg.log -o "-p 5433" start
/home/sjeva/.local/share/mamba/envs/postgres/bin/psql -h /tmp -p 5433 -U app_admin -d enterprise_ai

# Python venv
source /home/sjeva/enterprise-ai-platform/backend/.venv/bin/activate
# or
/home/sjeva/enterprise-ai-platform/backend/.venv/bin/python -m uvicorn app.main:app --reload --port 8000

# Backend API
cd /home/sjeva/enterprise-ai-platform/backend
python -m uvicorn app.main:app --reload --port 8000

# Frontend (to be created)
cd /home/sjeva/enterprise-ai-platform/frontend
pnpm dev

# Docker (when rootless is configured)
cd /home/sjeva/enterprise-ai-platform
docker compose up -d
```

---

## Blockers

1. **Rootless Docker not configured** — dockerd requires root or rootless mode. rootlesskit is installed but slirp4netns is not. Impact: `docker compose up -d` cannot be verified yet. Development continues with direct binary execution.
2. **Ollama download interrupted** — re-downloading in background.
3. **Hermes API server not enabled** — needs configuration.

---

## Next Actions

1. Complete project structure (backend modules, frontend scaffold)
2. Implement database models and migrations
3. Implement authentication and bootstrap
4. Implement ModelProvider abstraction
5. Implement chat API
6. Create frontend
7. Test end-to-end flow
