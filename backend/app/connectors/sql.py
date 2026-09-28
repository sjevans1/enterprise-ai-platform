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
import sqlglot
from sqlglot import exp as sqlglot_exp
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
            # Normalize async URL to sync URL for the sync engine
            db_url = self.db_url
            if db_url.startswith("postgresql+asyncpg://"):
                db_url = "postgresql://" + db_url[len("postgresql+asyncpg://"):]
            elif db_url.startswith("postgresql+psycopg://"):
                db_url = "postgresql://" + db_url[len("postgresql+psycopg://"):]
            # statement_timeout kills queries that run too long
            self._engine = create_engine(
                db_url,
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
    def validate_sql(sql: str, max_rows: int = 1000, allowed_tables: list[str] | None = None) -> list[str]:
        """Parse and validate that *sql* is read-only using SQLGlot AST.

        Uses SQLGlot (a semantic SQL parser) to analyze the AST, not just
        token patterns. This provides defense-in-depth against obfuscated
        queries that sqlparse would miss (e.g., nested INSERT INTO ... SELECT).

        Validation checks:
        1. Single statement only
        2. Must be a SELECT or WITH ... SELECT (no writes, DDL, etc.)
        3. No forbidden keywords anywhere in the AST
        4. If allowed_tables is provided, only those tables may be queried
        5. User-supplied LIMIT is capped at max_rows (via _ensure_limit)

        Returns the list of parsed statement strings (always exactly one
        for valid single-statement queries).
        """
        if not sql or not sql.strip():
            raise SQLValidationError("Empty query")

        # Parse with SQLGlot for semantic AST analysis
        try:
            parsed = sqlglot.parse(sql, read="postgres")
        except Exception as e:
            raise SQLValidationError(f"Failed to parse SQL: {e}")

        # Filter out empty statements
        statements = [s for s in parsed if s is not None]

        if not statements:
            raise SQLValidationError("Query is empty after parsing")

        if len(statements) != 1:
            raise SQLValidationError(
                f"Only single-statement queries are allowed (got {len(statements)})"
            )

        stmt = statements[0]

        # Check the statement type via AST — this catches obfuscated queries
        # that sqlparse would miss (e.g., CTE with embedded modification)
        stmt_type = type(stmt).__name__

        # Allowed root-level statement types
        ALLOWED_STMT_TYPES = {
            "Select",       # SELECT ...
            "With",         # WITH ... SELECT (CTE)
            "Subqueryable", # some edge cases
        }

        # Check for forbidden keywords in the full AST
        forbidden_found = []
        for node in stmt.walk():
            # Check for write operations
            if isinstance(node, sqlglot.exp.Insert):
                forbidden_found.append("INSERT")
            elif isinstance(node, sqlglot.exp.Update):
                forbidden_found.append("UPDATE")
            elif isinstance(node, sqlglot.exp.Delete):
                forbidden_found.append("DELETE")
            elif isinstance(node, sqlglot.exp.TruncateTable):
                forbidden_found.append("TRUNCATE")
            elif isinstance(node, (sqlglot.exp.Drop,)):
                forbidden_found.append("DROP")
            elif isinstance(node, (sqlglot.exp.Create,)):
                forbidden_found.append("CREATE")
            elif isinstance(node, (sqlglot.exp.Alter,)):
                forbidden_found.append("ALTER")
            elif isinstance(node, sqlglot.exp.Grant):
                forbidden_found.append("GRANT")
            elif isinstance(node, sqlglot.exp.Revoke):
                forbidden_found.append("REVOKE")
            elif isinstance(node, sqlglot.exp.Replace):
                forbidden_found.append("REPLACE")
            elif isinstance(node, sqlglot.exp.Attach):
                forbidden_found.append("ATTACH")
            elif isinstance(node, sqlglot.exp.Detach):
                forbidden_found.append("DETACH")
            elif isinstance(node, sqlglot.exp.Comment):
                forbidden_found.append("COMMENT")
            elif isinstance(node, sqlglot.exp.Execute):
                forbidden_found.append("EXECUTE")
            elif isinstance(node, sqlglot.exp.Declare):
                forbidden_found.append("DECLARE")
            elif isinstance(node, sqlglot.exp.SetItem):
                forbidden_found.append("SET")
            elif isinstance(node, sqlglot.exp.Cache):
                forbidden_found.append("CACHE")

        if forbidden_found:
            raise SQLValidationError(
                f"Forbidden operation(s) in query: {', '.join(set(forbidden_found))}. "
                f"Only SELECT and WITH ... SELECT are allowed."
            )

        # Check that root-level statement is a read operation
        if not isinstance(stmt, (sqlglot.exp.Select, sqlglot.exp.With)):
            # Also allow pure CTE-based queries that resolve to SELECT
            if isinstance(stmt, sqlglot.exp.Query):
                pass  # Query is the base class for Select, Union, etc.
            else:
                stmt_name = type(stmt).__name__
                raise SQLValidationError(
                    f"Only SELECT and WITH ... SELECT queries are allowed "
                    f"(got {stmt_name})"
                )

        # Enforce allowed table allowlist if provided
        if allowed_tables is not None:
            found_tables = set()
            for node in stmt.find_all(sqlglot.exp.Table):
                table_name = node.name.lower()
                if table_name not in [t.lower() for t in allowed_tables]:
                    found_tables.add(table_name)
            if found_tables:
                raise SQLValidationError(
                    f"Query references tables not in the allowlist: "
                    f"{', '.join(sorted(found_tables))}"
                )

        return [sql]

    @staticmethod
    def _ensure_limit(sql: str, max_rows: int) -> tuple[str, bool]:
        """Ensure a query's LIMIT does not exceed max_rows.

        If the query has no LIMIT clause, one is appended (limit_applied=True).
        If the query has a LIMIT clause that exceeds max_rows, it is capped.
        If the LIMIT is already within bounds, it is left as-is (limit_applied=False).

        Uses SQLGlot AST analysis for accurate LIMIT detection.

        Returns (modified_sql, limit_applied).
        """
        try:
            parsed = sqlglot.parse_one(sql, read="postgres")
        except Exception:
            # SQLGlot parse failed — fall back to regex for safety
            if re.search(r'\bLIMIT\s+\d+', sql, re.IGNORECASE):
                return sql, False
            sql = sql.rstrip().rstrip(';').rstrip()
            return f"{sql} LIMIT {max_rows}", True

        # Find LIMIT in the AST
        limit_expr = parsed.find(sqlglot.exp.Limit)

        if limit_expr is None:
            # No LIMIT — append one
            sql = sql.rstrip().rstrip(';').rstrip()
            return f"{sql} LIMIT {max_rows}", True

        # Check if the LIMIT value exceeds max_rows
        limit_num = limit_expr.find(sqlglot.exp.Literal)
        if limit_num is not None:
            try:
                current_limit = int(limit_num.name)
                if current_limit > max_rows:
                    # Cap the user-supplied LIMIT
                    limit_num.set("this", str(max_rows))
                    new_sql = parsed.sql(dialect="postgres")
                    return new_sql, True
            except (ValueError, AttributeError):
                pass

        # LIMIT already within bounds
        return sql, False

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
