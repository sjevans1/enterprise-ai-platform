"""SQL connector for structured data queries.

Provides a read-only PostgreSQL connector with:
- AST-based SQL validation (sqlparse) to block writes, DDL, and dangerous ops
- Bounded query execution (LIMIT, statement_timeout)
- Separate customer database connection (distinct from app DB account)

Usage:
    connector = SQLConnector(db_url="postgresql://user:pass@host/db")
    result = await connector.execute("SELECT * FROM customers LIMIT 10")
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import sqlparse
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.result import Row

logger = logging.getLogger(__name__)

# ── Disallowed statements ───────────────────────────────────────────
# These keywords indicate statements that must never be allowed
# via the query API.
FORBIDDEN_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "REPLACE", "TRUNCATE",
    "DROP", "CREATE", "ALTER", "GRANT", "REVOKE",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX",
    "COMMENT", "DECLARE", "EXECUTE", "PREPARE",
}

# ── Validation errors ────────────────────────────────────────────────
class SQLValidationError(ValueError):
    """Raised when a SQL query fails read-only validation."""


@dataclass
class QueryResult:
    """Structured result of a validated SQL query execution."""

    query: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    columns: list[str] = field(default_factory=list)
    execution_time_ms: float = 0.0
    limit_applied: bool = False


class SQLConnector:
    """Read-only PostgreSQL connector with SQL validation.

    Creates a dedicated engine for the customer database. In production,
    the database account used here has read-only privileges enforced at
    the PostgreSQL role level — this is defense in depth, not the only control.

    Parameters
    ----------
    db_url : str
        SQLAlchemy-compatible PostgreSQL URL for the customer database.
    schema : str
        Default schema to prefix unqualified table names (default: 'public').
    max_rows : int
        Maximum number of rows to return (default: 1000).
    query_timeout : int
        Per-query timeout in seconds (default: 30).
    """

    def __init__(
        self,
        db_url: str,
        schema: str = "public",
        max_rows: int = 1000,
        query_timeout: int = 30,
    ):
        self.db_url = db_url
        self.schema = schema
        self.max_rows = max_rows
        self.query_timeout = query_timeout
        self._engine: Engine | None = None

    @property
    def engine(self) -> Engine:
        """Lazy-initialize the SQLAlchemy engine (sync, for read-only queries)."""
        if self._engine is None:
            # statement_timeout kills queries that run too long
            self._engine = create_engine(
                self.db_url,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
                pool_recycle=3600,
                connect_args={
                    "options": f"-c statement_timeout={self.query_timeout * 1000}",
                },
            )
        return self._engine

    def close(self) -> None:
        """Dispose of the connection pool."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    # ── SQL validation ───────────────────────────────────────────────

    @staticmethod
    def validate_sql(sql: str) -> list[str]:
        """Parse and validate that *sql* is read-only.

        Uses sqlparse to split into statements and checks for forbidden
        keywords. Raises SQLValidationError on the first violation.

        Returns the list of parsed statement strings (always exactly one
        for valid single-statement queries).
        """
        if not sql or not sql.strip():
            raise SQLValidationError("Empty query")

        # Remove inline and block comments to prevent obfuscation
        cleaned = sqlparse.format(sql, strip_comments=True)

        # Parse into individual statements
        parsed = sqlparse.parse(cleaned)
        statements = [s for s in parsed if s.token_first()]

        # Only allow single-statement queries
        if len(statements) != 1:
            raise SQLValidationError(
                f"Only single-statement queries are allowed (got {len(statements)})"
            )

        stmt = statements[0]
        first_token = stmt.token_first()

        if first_token is None:
            raise SQLValidationError("Query is empty after parsing")

        # The first token's value (uppercased) determines the statement type
        stmt_type = first_token.value.upper().strip()

        # Check for forbidden statement types
        for keyword in FORBIDDEN_KEYWORDS:
            if stmt_type.startswith(keyword):
                raise SQLValidationError(
                    f"Forbidden statement type: '{stmt_type}'. "
                    f"Only SELECT is allowed."
                )

        # Double-check: ensure the statement is a SELECT
        # (covers edge cases like WITH ... SELECT)
        normalized = cleaned.upper().strip()
        if not (normalized.startswith("SELECT") or normalized.startswith("WITH")):
            # WITH is allowed (CTEs) but must ultimately be a SELECT
            if not normalized.startswith("WITH"):
                raise SQLValidationError(
                    f"Only SELECT and WITH ... SELECT queries are allowed"
                )

        # Check for forbidden keywords anywhere in the statement
        # (catches subqueries like INSERT INTO ... SELECT ...)
        tokens = [t.value.upper() for t in stmt.flatten() if t.is_keyword]
        for keyword in FORBIDDEN_KEYWORDS:
            if keyword in tokens:
                raise SQLValidationError(
                    f"Forbidden keyword '{keyword}' found in query"
                )

        return [str(s) for s in statements]

    @staticmethod
    def _ensure_limit(sql: str, max_rows: int) -> tuple[str, bool]:
        """Append a LIMIT clause if the query doesn't have one.

        Returns (modified_sql, limit_applied).
        """
        # Check for existing LIMIT (case-insensitive)
        if re.search(r'\bLIMIT\s+\d+', sql, re.IGNORECASE):
            return sql, False

        # Strip trailing semicolons and whitespace
        sql = sql.rstrip().rstrip(';').rstrip()

        # Append LIMIT
        return f"{sql} LIMIT {max_rows}", True

    # ── Query execution ──────────────────────────────────────────────

    async def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> QueryResult:
        """Validate, execute, and return results for a read-only SQL query.

        Parameters
        ----------
        sql : str
            The SQL query string (must be a single SELECT or WITH ... SELECT).
        params : dict, optional
            Named parameters for the query.

        Returns
        -------
        QueryResult
            Structured result with rows, column names, and timing.

        Raises
        ------
        SQLValidationError
            If the query fails read-only validation.
        Exception
            On database execution errors (timeout, permission denied, etc.).
        """
        import time
        from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

        # Validate SQL
        self.validate_sql(sql)

        # Ensure bounded results
        sql, limit_applied = self._ensure_limit(sql, self.max_rows)

        # Create async engine if we don't have one
        if not sql.startswith("postgresql+asyncpg"):
            async_url = self.db_url.replace("postgresql://", "postgresql+asyncpg://")
        else:
            async_url = self.db_url

        engine: AsyncEngine = create_async_engine(
            async_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            connect_args={
                "server_settings": {
                    "statement_timeout": str(self.query_timeout * 1000),
                },
            },
        )

        start = time.perf_counter()
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(sql),
                    params or {},
                )
                rows: list[dict[str, Any]] = [
                    dict(row) for row in result.mappings().all()
                ]
                columns = list(rows[0].keys()) if rows else []
                row_count = len(rows)
        finally:
            await engine.dispose()

        elapsed_ms = (time.perf_counter() - start) * 1000

        return QueryResult(
            query=sql,
            rows=rows,
            row_count=row_count,
            columns=columns,
            execution_time_ms=round(elapsed_ms, 2),
            limit_applied=limit_applied,
        )


# ── Singleton getter ─────────────────────────────────────────────────

from app.core.config import settings

_connector: SQLConnector | None = None


def get_sql_connector() -> SQLConnector:
    """Return a cached SQLConnector using the configured customer DB URL.

    The connector is created lazily on first use and cached for the
    lifetime of the process. The database URL comes from the
    ``STRUCTURED_DATA_DB_URL`` environment variable.
    """
    global _connector
    if _connector is None:
        if not settings.structured_data_db_url:
            raise RuntimeError(
                "STRUCTURED_DATA_DB_URL is not configured. "
                "Set it to your customer database connection string."
            )
        _connector = SQLConnector(
            db_url=settings.structured_data_db_url,
            schema=settings.structured_data_schema,
            max_rows=settings.structured_data_max_rows,
            query_timeout=settings.structured_data_query_timeout,
        )
    return _connector


def reset_sql_connector() -> None:
    """Reset the cached connector (used on shutdown or when config changes)."""
    global _connector
    if _connector is not None:
        _connector.close()
    _connector = None
