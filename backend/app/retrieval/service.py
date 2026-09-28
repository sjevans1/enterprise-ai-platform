"""Retrieval service: vector search + MMR reranking with ranked citations.

Workflow:
  1. Embed the query text using the local embedding provider.
  2. Search the pgvector IVFFlat index for top-K candidates
     (L2 distance uses the index; cosine similarity is recomputed
     during MMR for precise relevance scoring).
  3. Apply MMR reranking to balance relevance and diversity.
  4. Return ranked RetrievalResult objects with citation metadata.

Note on index metric:
  The existing IVFFlat index on document_chunks.embedding_vector uses
  pgvector's default metric (L2 distance). We use l2_distance for the
  initial candidate retrieval to leverage the index, then compute exact
  cosine similarity during MMR for final ranking.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings.provider import LocalEmbeddingProvider, get_embedding_provider
from app.retrieval.mmr import mmr_rerank
from app.documents.schemas import Citation, RetrievalResult

logger = logging.getLogger(__name__)


@dataclass
class VectorSearchResult:
    """Raw result from the vector database search."""

    chunk_id: str
    document_id: str
    chunk_index: int
    content: str
    page_ref: int | None
    section_ref: str | None
    row_ref: int | None
    embedding: list[float]
    l2_distance: float  # lower = more similar


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two 1-D numpy arrays."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _parse_vector(vec) -> list[float]:
    """Parse a pgvector value into a list of floats.

    pgvector returns the vector as a string like '[0.1, 0.2, ...]'
    when queried via raw SQL. This parser handles that format.
    """
    if vec is None:
        return []
    if isinstance(vec, (list, tuple)):
        return [float(x) for x in vec]
    # String format from raw SQL: '[0.1, 0.2, 0.3, ...]'
    s = str(vec).strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    elif s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    values: list[float] = []
    for part in s.split(","):
        part = part.strip()
        if part:
            try:
                values.append(float(part))
            except ValueError:
                pass
    return values


class RetrievalService:
    """Vector search + MMR retrieval over the document_chunks table.

    Uses the existing async session and pgvector IVFFlat index.
    All embedding computation is CPU-only via the local embedding provider.
    """

    def __init__(
        self,
        db_session: AsyncSession,
        embedding_provider: LocalEmbeddingProvider | None = None,
    ):
        self._db = db_session
        self._embedding = embedding_provider

    def _get_embedding_provider(self) -> LocalEmbeddingProvider:
        """Lazily load the embedding provider."""
        if self._embedding is None:
            self._embedding = get_embedding_provider()
        return self._embedding

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        fetch_k: int = 50,
        lambda_param: float = 0.7,
        document_ids: list[str] | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve documents using MMR-enhanced vector search.

        Args:
            query: Natural language search query.
            top_k: Number of final results to return (after MMR).
            fetch_k: Number of vector candidates to retrieve before MMR.
            lambda_param: MMR trade-off. 1.0 = pure relevance, 0.0 = pure diversity.
            document_ids: Optional list of document IDs to restrict search scope.

        Returns:
            List of RetrievalResult objects, ranked by MMR score.
        """
        if not query.strip():
            return []

        embedding_provider = self._get_embedding_provider()

        # Step 1: Embed the query
        query_embedding = await asyncio.to_thread(
            embedding_provider.embed_text, query
        )
        logger.debug(f"Query embedded: {len(query_embedding)} dims")

        query_vec = list(query_embedding)

        # Step 2: Vector search for candidates using pgvector
        # Use L2 distance (<#>) which leverages the IVFFlat index.
        # Cosine similarity is recomputed during MMR for final scoring.
        where_clauses: list[str] = ["embedding_vector IS NOT NULL"]
        params: dict = {
            "query_vec": f"[{','.join(str(x) for x in query_vec)}]",
            "limit": fetch_k,
        }

        if document_ids:
            where_clauses.append("document_id = ANY(:doc_ids)")
            params["doc_ids"] = document_ids

        where_sql = " AND ".join(where_clauses)

        sql = text(f"""
            SELECT
                dc.id,
                dc.document_id,
                dc.chunk_index,
                dc.content,
                dc.page_ref,
                dc.section_ref,
                dc.row_ref,
                dc.embedding_vector,
                dc.embedding_vector <#> CAST(:query_vec AS vector(384)) AS l2_dist
            FROM document_chunks dc
            WHERE {where_sql}
            ORDER BY dc.embedding_vector <#> CAST(:query_vec AS vector(384))
            LIMIT :limit
        """)

        # Set ivfflat probes for better recall on IVFFlat index (100 lists)
        await self._db.execute(text("SET LOCAL ivfflat.probes = 10"))

        result = await self._db.execute(sql, params)
        rows = result.fetchall()

        if not rows:
            logger.info(f"No results for query: '{query[:50]}...'")
            return []

        # Parse results into VectorSearchResult objects
        candidates: list[VectorSearchResult] = []
        for row in rows:
            emb = _parse_vector(row.embedding_vector)
            candidates.append(VectorSearchResult(
                chunk_id=row.id,
                document_id=row.document_id,
                chunk_index=row.chunk_index,
                content=row.content,
                page_ref=row.page_ref,
                section_ref=row.section_ref,
                row_ref=row.row_ref,
                embedding=emb,
                l2_distance=float(row.l2_dist),
            ))

        logger.info(
            f"Vector search returned {len(candidates)} candidates "
            f"for query: '{query[:50]}...'"
        )

        # Step 3: Apply MMR reranking
        # Convert cosine similarity to the [-1, 1] range for MMR
        # MMR internally normalizes vectors and computes cosine similarity
        candidate_embeddings = [c.embedding for c in candidates]
        candidate_indices = list(range(len(candidates)))

        ranked = mmr_rerank(
            query_embedding=query_vec,
            candidate_embeddings=candidate_embeddings,
            candidate_indices=candidate_indices,
            top_k=top_k,
            lambda_param=lambda_param,
        )

        # Step 4: Build RetrievalResult objects with citations
        results: list[RetrievalResult] = []
        for idx_in_candidates, mmr_score in ranked:
            candidate = candidates[idx_in_candidates]
            # Compute cosine similarity for relevance score
            cos_sim = _cosine_similarity(
                np.array(query_vec), np.array(candidate.embedding)
            )
            # Map from [-1, 1] to [0, 1] for a 0-1 relevance score
            relevance_score = max(0.0, min(1.0, (cos_sim + 1.0) / 2.0))

            citation = Citation(
                source_type="document",
                source_id=candidate.document_id,
                title=self._format_title(candidate),
                passage=candidate.content[:500],
                citation=self._format_citation(candidate),
                confidence=round(relevance_score, 4),
            )

            results.append(RetrievalResult(
                chunk_id=candidate.chunk_id,
                document_id=candidate.document_id,
                relevance_score=round(relevance_score, 4),
                mmr_score=round(float(mmr_score), 4),
                content=candidate.content,
                page_ref=candidate.page_ref,
                section_ref=candidate.section_ref,
                row_ref=candidate.row_ref,
                metadata={
                    "chunk_index": candidate.chunk_index,
                    "l2_distance": round(candidate.l2_distance, 6),
                    "source": "vector_mmr",
                },
                citation=citation,
            ))

        return results

    async def retrieve_simple(
        self,
        query: str,
        top_k: int = 10,
        lambda_param: float = 0.7,
        document_ids: list[str] | None = None,
    ) -> list[RetrievalResult]:
        """Simplified retrieval with sensible defaults for fetch_k.

        fetch_k is set to top_k * 5 (capped at 500) to provide enough
        candidates for MMR while keeping latency reasonable.
        """
        fetch_k = min(max(top_k * 5, 10), 500)
        return await self.retrieve(
            query=query,
            top_k=top_k,
            fetch_k=fetch_k,
            lambda_param=lambda_param,
            document_ids=document_ids,
        )

    # ── Internal helpers ───────────────────────────────────────────

    def _format_title(self, candidate: VectorSearchResult) -> str | None:
        """Build a human-readable title from chunk metadata."""
        parts: list[str] = []
        if candidate.page_ref is not None:
            parts.append(f"p.{candidate.page_ref}")
        if candidate.section_ref:
            parts.append(candidate.section_ref)
        if candidate.row_ref is not None:
            parts.append(f"row {candidate.row_ref}")
        return " · ".join(parts) if parts else None

    def _format_citation(self, candidate: VectorSearchResult) -> str:
        """Build a formatted citation string for display."""
        parts = [f"Chunk #{candidate.chunk_index}"]
        if candidate.page_ref is not None:
            parts.append(f"page {candidate.page_ref}")
        if candidate.section_ref:
            parts.append(f"§{candidate.section_ref}")
        if candidate.row_ref is not None:
            parts.append(f"row {candidate.row_ref}")
        return f"Document:{candidate.document_id[:8]} · " + " · ".join(parts)
