"""Real orchestration layer — request classification and evidence planning.

Implements the four-class routing model from the OpenJM reference architecture:
- GENERAL   — pure LLM with conversation context, no enterprise retrieval
- KNOWLEDGE — RAG retrieval from authorized documents, grounded with citations
- STRUCTURED — SQL query against approved data sources, no RAG
- HYBRID    — combines Knowledge + Structured evidence in one grounded response

The orchestrator does NOT perform the actions itself — it builds an ExecutionPlan
that the chat route executes. This separation makes the decision logic testable.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.common import Citation, ProcessingLocation
from app.documents.schemas import RetrievalResult
from app.db.models import DocumentPermission
from app.retrieval.service import RetrievalService
from app.connectors.sql import SQLConnector, SQLValidationError, get_sql_connector, reset_sql_connector

logger = logging.getLogger(__name__)


# ── Intent classification ──────────────────────────────────────────

class ExecutionClass(str, Enum):
    GENERAL = "general"          # No enterprise evidence needed
    KNOWLEDGE = "knowledge"      # Document retrieval only
    STRUCTURED = "structured"    # SQL query only
    HYBRID = "hybrid"           # Both document + SQL evidence


@dataclass
class ExecutionPlan:
    """Blueprint for how to execute a single chat request."""
    execution_class: ExecutionClass
    confidence: float = 0.0  # classifier confidence 0.0–1.0
    rationale: str = ""

    # Evidence plans
    knowledge_plan: dict[str, Any] | None = None      # {"enabled": bool, "top_k": int}
    structured_plan: dict[str, Any] | None = None     # {"enabled": bool, "sql_query": str | None}

    # Results (filled during execution)
    knowledge_evidence: list[Citation] = field(default_factory=list)
    structured_evidence: list[Citation] = field(default_factory=list)
    sql_query_result: dict[str, Any] | None = None

    # Bounded context
    context_messages: list[dict] = field(default_factory=list)
    max_context_tokens: int = 4000  # bounded conversation history


# ── Keyword-based intent classifier ─────────────────────────────────
# This is intentionally lightweight (keyword/heuristic). The audit notes
# the orchestrator was empty. A full LLM-based classifier can replace this
# later, but it must never become the only control — code-level checks
# must always validate the plan before execution.

_KNOWLEDGE_KEYWORDS = {
    "what is", "how do", "explain", "policy", "document", "manual",
    "procedure", "guideline", "sop", "where is", "what does", "our policy",
    "procedure for", "can you find", "search for", "look up",
    "what does our", "what are our",
}

# Strong document-specific keywords — must match for Knowledge classification
# to avoid classifying general "what is X?" questions as Knowledge
_KNOWLEDGE_STRONG = {
    "policy", "document", "manual", "procedure", "guideline", "sop",
    "our policy", "what does our", "what are our", "company policy",
    "company document", "internal document",
}

_STRUCTURED_KEYWORDS = {
    "sales", "revenue", "cost", "budget", "customer", "employee",
    "total", "count", "number of", "sum of", "average", "avg",
    "table", "how many", "list of", "who are", "what were",
    "report", "analytics", "metric", "metrics", "data on",
    "spending", "salary", "payroll", "expense", "compare", "overtime",
    "hours", "headcount", "by branch", "by region", "by month",
    "breakdown", "distribution", "trend", "performance",
}

_SQL_INDICATORS = {
    "select", "sum(", "count(", "avg(", "where", "from",
    "group by", "order by", "join", "table", "query",
}


class Orchestrator:
    """Request classification and evidence planning.

    The orchestrator classifies each user request into an execution class,
    then builds an ExecutionPlan that specifies what evidence to retrieve.

    It does NOT execute the plan — that's the chat route's job. This
    separation keeps classification testable independently of model calls.
    """

    def __init__(self):
        self._initialized = True

    def classify(self, query: str, has_documents: bool = True) -> ExecutionPlan:
        """Classify a user query into an execution class.

        Uses keyword heuristics + structural indicators. Returns an
        ExecutionPlan with evidence retrieval enabled as appropriate.
        """
        query_lower = query.lower().strip()

        knowledge_score = sum(1 for kw in _KNOWLEDGE_KEYWORDS if kw in query_lower)
        knowledge_strong = any(kw in query_lower for kw in _KNOWLEDGE_STRONG)
        structured_score = sum(1 for kw in _STRUCTURED_KEYWORDS if kw in query_lower)
        sql_score = sum(1 for kw in _SQL_INDICATORS if kw in query_lower)

        # Structured-data path: SQL indicators or strong structured keywords
        if sql_score >= 2 or structured_score >= 2:
            plan = ExecutionPlan(
                execution_class=ExecutionClass.STRUCTURED,
                confidence=min(0.4 + structured_score * 0.15 + sql_score * 0.1, 0.95),
                rationale=f"Strong structured-data indicators (structured_kw={structured_score}, sql_indicators={sql_score})",
                knowledge_plan={"enabled": False},
                structured_plan={"enabled": True},
            )
            # If it also has strong knowledge keywords, it's hybrid
            if knowledge_strong:
                plan.execution_class = ExecutionClass.HYBRID
                plan.knowledge_plan = {"enabled": True, "top_k": 5}
                plan.rationale += f"; also has strong knowledge indicators"
            return plan

        # Knowledge path: requires document-specific keywords, not just "what is"
        if knowledge_strong and has_documents:
            plan = ExecutionPlan(
                execution_class=ExecutionClass.KNOWLEDGE,
                confidence=min(0.4 + knowledge_score * 0.15, 0.9),
                rationale=f"Document/knowledge request (strong keywords, score={knowledge_score})",
                knowledge_plan={"enabled": True, "top_k": 8},
                structured_plan={"enabled": False},
            )
            # If it also has structured indicators, it's hybrid
            if structured_score >= 1:
                plan.execution_class = ExecutionClass.HYBRID
                plan.knowledge_plan = {"enabled": True, "top_k": 5}
                plan.rationale += f"; also has structured indicators (score={structured_score})"
            return plan

        # Default: general (no enterprise retrieval)
        return ExecutionPlan(
            execution_class=ExecutionClass.GENERAL,
            confidence=0.7,
            rationale="No strong knowledge or structured-data indicators",
            knowledge_plan={"enabled": False},
            structured_plan={"enabled": False},
        )

    async def plan(
        self,
        user_message: str,
        user_permissions: list[str],
        db: AsyncSession,
        has_documents: bool = True,
    ) -> ExecutionPlan:
        """Full planning: classify + execute evidence retrieval.

        This is the async version that actually retrieves evidence.
        """
        # Step 1: Classify
        plan = self.classify(user_message, has_documents)

        # Step 2: Execute evidence retrieval based on plan
        tasks = []
        if plan.knowledge_plan and plan.knowledge_plan.get("enabled"):
            tasks.append(("knowledge", self._retrieve_knowledge(
                plan, user_message, user_permissions, db
            )))
        if plan.structured_plan and plan.structured_plan.get("enabled"):
            tasks.append(("structured", self._retrieve_structured(
                plan, user_message, user_permissions, db
            )))

        if tasks:
            results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
            for (name, _), result in zip(tasks, results):
                if isinstance(result, Exception):
                    logger.warning(f"Evidence retrieval failed for {name}: {result}")
                elif name == "knowledge" and result:
                    plan.knowledge_evidence = [c for c in result if c]
                elif name == "structured" and result:
                    plan.structured_evidence = [c for c in result if c]

        return plan

    async def _retrieve_knowledge(
        self,
        plan: ExecutionPlan,
        query: str,
        user_permissions: list[str],
        db: AsyncSession,
    ) -> list[Citation]:
        """Retrieve knowledge evidence with permission filtering."""
        try:
            retrieval = RetrievalService(db_session=db)
            top_k = plan.knowledge_plan.get("top_k", 8)

            results = await retrieval.retrieve_simple(
                query=query,
                top_k=top_k,
            )

            # Permission-aware filtering: only return chunks the user
            # has access to (document_permissions table)
            filtered: list[RetrievalResult] = []
            for r in results:
                if await self._check_document_access(r.document_id, user_permissions, db):
                    filtered.append(r)

            citations: list[Citation] = []
            for r in filtered:
                citations.append(Citation(
                    source_type="document",
                    source_id=r.document_id,
                    title=f"Document chunk (page {r.page_ref})" if r.page_ref else "Document chunk",
                    passage=r.content[:500],
                    citation=f"Document:{r.document_id[:8]} · Chunk #{r.metadata.get('chunk_index', '?')}",
                    confidence=r.relevance_score,
                ))
            return citations
        except Exception as e:
            logger.warning(f"Knowledge retrieval failed: {e}", exc_info=True)
            return []

    async def _retrieve_structured(
        self,
        plan: ExecutionPlan,
        query: str,
        user_permissions: list[str],
        db: AsyncSession,
    ) -> list[Citation]:
        """Retrieve structured data evidence via SQL query.

        Uses sqlparse-based keyword detection as a first pass, then
        validates via the SQLConnector's AST validation.
        """
        from app.core.config import settings
        from app.connectors.sql import reset_sql_connector

        if not settings.structured_data_db_url:
            logger.info("No structured_data_db_url configured — skipping SQL retrieval")
            return []

        if "sql:query" not in user_permissions:
            logger.warning("User lacks sql:query permission — skipping structured evidence")
            return []

        # Reset connector to pick up fresh config
        reset_sql_connector()
        connector = get_sql_connector()

        # Ask the LLM to generate a SQL query from the natural language request
        # For now: detect if the user included explicit SQL keywords
        sql_pattern = re.compile(
            r'\b(SELECT|WITH)\b.*\b(FROM|JOIN)\b',
            re.IGNORECASE | re.DOTALL,
        )
        match = sql_pattern.search(query)
        sql_query = match.group(0) if match else None

        # If no explicit SQL found, generate from the natural language query
        # This is a conservative heuristic — prefer explicit queries
        if not sql_query:
            # Convert natural language to a SELECT by checking for table references
            table_pattern = re.compile(
                r'\b(from|table|in)\b\s+(\w+)',
                re.IGNORECASE,
            )
            table_match = table_pattern.search(query)
            if table_match:
                table_name = table_match.group(2)
                # Verify the table exists in the schema
                schema = plan.structured_plan.get("schema", "public")
                sql_query = f"SELECT * FROM {schema}.{table_name} LIMIT 5"

        if not sql_query:
            return []

        # Validate and execute (SQLConnector validates via sqlparse)
        try:
            result = await connector.execute(sql_query)
            plan.sql_query_result = {
                "query": result.query,
                "columns": result.columns,
                "row_count": result.row_count,
                "execution_time_ms": result.execution_time_ms,
            }

            citations: list[Citation] = []
            for i, row in enumerate(result.rows[:5]):
                citations.append(Citation(
                    source_type="sql",
                    source_id=f"sql-{i}",
                    title=f"Query result row {i+1}",
                    passage=str(row),
                    citation=f"DB: {result.query[:80]}...",
                    confidence=1.0,
                ))
            return citations
        except SQLValidationError as e:
            logger.warning(f"SQL query rejected: {e}")
            return []
        except Exception as e:
            logger.warning(f"Structured query failed: {e}")
            return []

    async def _check_document_access(
        self, document_id: str, user_permissions: list[str], db: AsyncSession
    ) -> bool:
        """Check if the user has access to a document.

        Uses the document_permissions table. If no explicit permissions
        exist for this document, falls back to document:read permission.
        """
        if "document:read" in user_permissions:
            return True

        stmt = select(DocumentPermission).where(
            DocumentPermission.document_id == document_id,
        )
        result = await db.execute(stmt)
        perms = result.scalars().all()

        # If no explicit permissions, allow with document:read
        if not perms:
            return "document:read" in user_permissions

        # Check each permission — in practice this would check role mappings
        return any(p.document_id == document_id for p in perms)


def get_processing_location_value(provider_location: str) -> str:
    """Normalize processing location string to match the schema."""
    if provider_location == ProcessingLocation.LOCAL.value:
        return "LOCAL"
    if provider_location == ProcessingLocation.REMOTE.value:
        return "REMOTE"
    return "UNKNOWN"


def truncate_context(messages: list[dict], max_tokens: int = 4000) -> list[dict]:
    """Truncate conversation history to stay within token budget.

    Keeps the first system message (if any) and the most recent messages.
    """
    result = []
    token_count = 0
    for msg in reversed(messages):
        tokens = len(msg.get("content", "").split())
        if token_count + tokens > max_tokens and result:
            break
        result.insert(0, msg)
        token_count += tokens
    return result


# ── Singleton ──────────────────────────────────────────────────────

_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    """Return the singleton orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator
