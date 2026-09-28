# Hardware Requirements for Embedding Generation

## Overview

The Enterprise AI Platform MVP uses **`sentence-transformers/all-MiniLM-L6-v2`** as its
local, CPU-only embedding model. This document records the hardware requirements,
performance characteristics, and resource planning notes per the platform spec
(Spec section 15: "Record its name, revision, dimensions, licence and preprocessing/query instructions").

## Model Details

| Field                | Value                                      |
|----------------------|--------------------------------------------|
| Model name           | `sentence-transformers/all-MiniLM-L6-v2`   |
| Architecture         | Transformer (distilled BERT, 6 layers)      |
| Hidden size          | 384                                        |
| Attention heads      | 12                                         |
| Intermediate size    | 2304                                       |
| Max sequence length  | 256 tokens                                 |
| Parameters           | ~22.8M                                     |
| Embedding dimensions | 384                                        |
| License              | Apache-2.0                                 |
| Preprocessing        | Lowercase, tokenize, truncate to 256 tokens, mean pooling |
| Query instructions   | "Represent the question for retrieval:"    |

## CPU-Only Requirements

### Minimum (Development / Single-user)

| Component | Requirement                         |
|-----------|--------------------------------------|
| CPU       | 4 cores (Intel i5 / AMD Ryzen 5)    |
| RAM       | 8 GB system memory                  |
| Disk      | 500 MB free for model cache + 1 GB per 1k documents |
| Network   | One-time model download (~90 MB)    |

Performance: ~1,000–5,000 docs/min (batch=32)

### Recommended (Production / Multi-user)

| Component | Requirement                         |
|-----------|--------------------------------------|
| CPU       | 8+ cores (Intel Xeon / AMD EPYC)    |
| RAM       | 16+ GB system memory                |
| Disk      | NVMe SSD, 1 GB+ model cache, scalable storage for uploads |
| Network   | Local model cache (no external calls) |

Performance: ~5,000–20,000 docs/min (batch=32, 8 cores)

### GPU Acceleration (Optional)

| Component | Requirement                         |
|-----------|--------------------------------------|
| GPU       | NVIDIA GPU with 4+ GB VRAM (CUDA)   |
| Driver    | CUDA 11.x or 12.x compatible        |

Note: The platform is designed for CPU-only operation. GPU acceleration is
not required and can be disabled by not installing a CUDA-enabled torch.

## Resource Planning

### Memory Footprint

| Component                    | Memory Use          |
|------------------------------|---------------------|
| Model weights (in RAM)       | ~90 MB              |
| Per batch (32 chunks × 384)  | ~50 KB              |
| Python process overhead      | ~200–500 MB         |
| Upload file buffer (50 MB)   | ~50 MB per upload   |

### Model Cache

- **Location:** `.cache/embeddings` (configurable via `embedding_cache_dir`)
- **Size:** ~90 MB (one-time download)
- **Persistence:** Cached across restarts via `sentence-transformers` cache

### Vector Storage in PostgreSQL

Each `DocumentChunk` stores a `Vector(384)` column (pgvector). Storage cost:

| Per chunk | 384 × 4 bytes (float32) ≈ 1.5 KB raw |
|-----------|--------------------------------------|
| Per 1,000 chunks | ~1.5 MB + overhead ≈ 3 MB |
| Per 100,000 chunks | ~300 MB |

Index: IVFFlat with 100 lists (`lists='100'`).
Recommended `ivfflat.probes = 10` for balanced speed/accuracy.

### Ingestion Throughput (CPU-only, approximate)

| Document | Chunks | Wall time   |
|----------|--------|-------------|
| 10-page PDF   | ~50 chunks    | 2–5 seconds |
| 50-page PDF   | ~200 chunks   | 5–15 seconds |
| 100-page DOCX | ~300 chunks   | 10–20 seconds |

These times include extraction, chunking, embedding generation, and DB writes.
Embedding generation dominates (90%+ of time). Batching (`batch_size=32`)
amortizes model loading overhead.

## Operational Notes

- **No remote calls:** All embedding computation is local. No API keys or
  network egress during inference.
- **Model loading:** Lazy-loaded on first request (~2–5 seconds cold start).
  Subsequent requests reuse the cached model.
- **Thread safety:** `sentence-transformers` models are not fully thread-safe
  under high concurrency. The ingestion service uses `asyncio.to_thread()`
  to isolate model access.
- **Scaling:** For higher throughput, run multiple worker processes, each
  with its own model instance. Memory = N_workers × (model + overhead).

## References

- Model card: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
- pgvector docs: https://github.com/pgvector/pgvector
- sentence-transformers docs: https://www.sbert.net/
