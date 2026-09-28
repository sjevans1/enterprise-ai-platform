-- ─── PostgreSQL initialization script ─────────────────────────────────
-- Runs inside the pgvector container on first startup
-- Creates the application's database account (separate from customer DBs)
-- ──────────────────────────────────────────────────────────────────────

-- Create the application database
CREATE DATABASE enterprise_ai;

-- Switch to the app database for schema-level setup
\c enterprise_ai
