"""API routes for structured data query.

Provides read-only SQL query execution against a customer PostgreSQL
database with AST-based validation (sqlparse), bounded results, and
query evidence recording for use in grounded chat responses.

All queries must be SELECT or WITH ... SELECT. Write/DDL/DCL operations
are rejected at parse time before any database round-trip occurs.
"""
from __future__ import annotations

import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_permission
from app.api.schemas.structured_data import (
    SQLQueryRequest,
    SQLQueryResponse,
    SchemaResponse,
    TableInfo,
    EvidenceRecord,
)
from app.connectors.sql import SQLConnector, SQLValidationError, get_sql_connector
from app.core.config import settings
from app.core.permissions import Permission

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/structured-data",
    tags=["structured-data"],
)


# ── Dependency ──────────────────────────────────────────────────────

def get_connector() -> SQLConnector:
    """Get the SQL connector (or raise if DB URL not configured)."""
    try:
        return get_sql_connector()
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )


# ── Query execution ────────────────────────────────────────────────

@router.post("/query", response_model=SQLQueryResponse)
async def execute_query(
    request: SQLQueryRequest,
    user=Depends(require_permission(Permission.SQL_QUERY.value)),
    connector: SQLConnector = Depends(get_connector),
):
    """Execute a read-only SQL query against the customer database.

    The query is validated via AST inspection before execution:
    - Only single-statement SELECT or WITH ... SELECT queries are allowed
    - INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, etc. are rejected
    - Results are capped at ``max_rows`` (default: 1000)
    - Queries are killed after ``query_timeout`` seconds

    **Required permissions:** ``sql:query``
    """
    logger.info("Structured data query by user %s: %s", user.email, request.query[:100])

    try:
        result = await connector.execute(request.query, request.params)
    except SQLValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error("Query execution failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Query execution failed",
        )

    return SQLQueryResponse(
        query=result.query,
        columns=result.columns,
        row_count=result.row_count,
        rows=result.rows,
        execution_time_ms=result.execution_time_ms,
        limit_applied=result.limit_applied,
        max_rows=connector.max_rows,
    )


# ── Schema introspection ───────────────────────────────────────────

@router.get("/schema", response_model=SchemaResponse)
async def get_schema(
    user=Depends(require_permission(Permission.SQL_SCHEMA.value)),
    connector: SQLConnector = Depends(get_connector),
):
    """List tables and their metadata in the customer database.

    Uses the customer database connector's engine — NOT the platform's
    application database session (AsyncSessionLocal).

    **Required permissions:** ``sql:schema``
    """
    from sqlalchemy import text

    with connector.engine.connect() as conn:
        # Get table names from the customer database
        result = conn.execute(
            text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = :schema
                AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """),
            {"schema": connector.schema},
        )
        tables = [r[0] for r in result.fetchall()]

        table_infos = []
        for tname in tables:
            # Get column count AND row estimate in a single query
            count_result = conn.execute(
                text("""
                    SELECT 
                        (SELECT COUNT(*) FROM information_schema.columns
                         WHERE table_name = :t AND table_schema = :schema) as col_count,
                        (SELECT reltuples::bigint FROM pg_class 
                         WHERE relname = :t) as row_est
                """),
                {"t": tname, "schema": connector.schema},
            )
            row = count_result.fetchone()
            table_infos.append(TableInfo(
                table_name=tname,
                column_count=row[0] if row else 0,
                row_estimate=row[1] if row else None,
            ))

    return SchemaResponse(
        schema=connector.schema,
        tables=table_infos,
    )


# ── Evidence generation ────────────────────────────────────────────

@router.post("/query/evidence", response_model=EvidenceRecord)
async def query_for_evidence(
    request: SQLQueryRequest,
    user=Depends(require_permission(Permission.SQL_QUERY.value)),
    connector: SQLConnector = Depends(get_connector),
):
    """Execute a query and return it as a structured evidence record.

    This endpoint is used by the chat/orchestration layer to gather
    evidence from the customer database for grounded responses.
    The result is wrapped in an ``EvidenceRecord`` that can be cited
    in chat responses.

    **Required permissions:** ``sql:query``
    """
    logger.info("Evidence query by user %s: %s", user.email, request.query[:100])

    try:
        result = await connector.execute(request.query, request.params)
    except SQLValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error("Evidence query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Query execution failed",
        )

    # Build citation string
    citation = f"Query returned {result.row_count} rows in {result.execution_time_ms}ms.\n\n"
    if result.rows:
        for row in result.rows[:10]:
            citation += str(row) + "\n"

    evidence_id = f"sql-{uuid.uuid4().hex[:8]}"

    return EvidenceRecord(
        source_type="sql_query",
        source_id=evidence_id,
        title=f"Database query: {request.query[:50]}...",
        content=citation,
        snippet=request.query,
        metadata={
            "columns": result.columns,
            "row_count": result.row_count,
            "execution_time_ms": result.execution_time_ms,
        },
    )
