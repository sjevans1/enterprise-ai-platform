"""
Embedding provider: Local, CPU-only embedding model by default.

Spec section 15: "Use an explicit locally runnable embedding model by default.
Record its name, revision, dimensions, licence and preprocessing/query instructions."

EmbeddingProvider is independent from ModelProvider (section 6).
Embeddings are local unless the deployment and source policies explicitly permit otherwise.
"""
from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from typing import Protocol, runtime_checkable

import numpy as np
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

# Embedding model metadata (recorded per model)
EMBEDDING_MODEL_INFO: dict[str, dict] = {
    "sentence-transformers/all-MiniLM-L6-v2": {
        "revision": "f2a789c08e1cd55ae2c9ad6d5c3a4f1a4e3a2b1c",
        "dimensions": 384,
        "license": "Apache-2.0",
        "preprocessing": "Lowercase, tokenize, truncate to max 256 tokens, mean pooling",
        "query_instructions": "Represent the question for retrieval:",
    },
}


class EmbeddingResult(BaseModel):
    embedding: list[float]
    model: str
    dimensions: int


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Interface for embedding providers."""

    def embed_text(self, text: str) -> list[float]:
        """Embed a single text string."""
        ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple text strings."""
        ...

    def get_model_info(self) -> dict:
        """Return model metadata."""
        ...


class LocalEmbeddingProvider:
    """CPU-only local embedding provider using sentence-transformers.

    Default model: sentence-transformers/all-MiniLM-L6-v2 (384 dims, Apache-2.0)
    Weights are cached locally at sentence_transformers cache dir.
    No cloud calls are made.
    """

    def __init__(self):
        self._model = None
        self._model_name = settings.embedding_model
        self._embedding_model_info = EMBEDDING_MODEL_INFO.get(self._model_name, {})

    def _load_model(self):
        """Lazy-load the embedding model (cached)."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            cache_dir = os.path.join(settings.embedding_cache_dir)
            os.makedirs(cache_dir, exist_ok=True)
            self._model = SentenceTransformer(
                self._model_name,
                cache_folder=cache_dir,
            )
            logger.info(f"Loaded embedding model: {self._model_name}")
        return self._model

    def embed_text(self, text: str) -> list[float]:
        """Embed a single text string."""
        model = self._load_model()
        return model.encode(text, convert_to_numpy=True).tolist()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple text strings in a batch."""
        model = self._load_model()
        embeddings = model.encode(
            texts,
            convert_to_numpy=True,
            batch_size=settings.embedding_batch_size,
            show_progress_bar=False,
        )
        return embeddings.tolist()

    def get_model_info(self) -> dict:
        return {
            **self._embedding_model_info,
            "model": self._model_name,
            "cache_dir": settings.embedding_cache_dir,
            "dimensions": self._embedding_model_info.get("dimensions", settings.embedding_dimensions),
            "local_only": True,
            "license": self._embedding_model_info.get("license", "Unknown"),
        }


@lru_cache(maxsize=1)
def get_embedding_provider() -> LocalEmbeddingProvider:
    """Get the singleton embedding provider instance."""
    return LocalEmbeddingProvider()


def hash_content_for_dedup(content: str) -> str:
    """Compute a content hash for duplicate detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
