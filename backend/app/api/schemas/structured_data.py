"""Pydantic schemas for structured data query endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SQLQueryRequest(BaseModel):
    """Request body for executing a SQL query."""

    query: str = Field(..., min_length=1, max_length=5000,
                       description="SQL query (SELECT or WITH ... SELECT only)")
    params: dict[str, Any] | None = Field(default=None,
                                           description="Named query parameters")


class QueryResultRow(BaseModel):
    """A single row of query results."""

    data: dict[str, Any]


class SQLQueryResponse(BaseModel):
    """Response from a validated SQL query execution."""

    query: str
    columns: list[str]
    row_count: int
    rows: list[dict[str, Any]]
    execution_time_ms: float
    limit_applied: bool
    max_rows: int


class TableInfo(BaseModel):
    """Metadata about a table in the customer database."""

    table_name: str
    column_count: int
    row_estimate: int | None = None


class SchemaResponse(BaseModel):
    """Response listing available tables and columns."""

    schema: str
    tables: list[TableInfo]


class EvidenceRecord(BaseModel):
    """A piece of evidence from a structured data query, usable in chat."""

    source_type: str = "sql_query"
    source_id: str
    title: str
    content: str
    snippet: str | None = None
    metadata: dict[str, Any] | None = None
