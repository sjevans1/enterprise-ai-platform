"""
ModelProvider: Abstract interface for LLM model interaction.

All communication between application services and model providers goes through
this interface. Provider-specific behaviour is isolated inside concrete adapters.

Supports:
  - Non-streaming and streaming chat completions
  - Normalization of provider errors and rate limits
  - Token usage tracking
  - Cancellation and deadlines
  - Capability discovery (streaming, structured output, tool calling, context limits)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Protocol, runtime_checkable
from uuid import uuid4


class ProcessingLocation(str, Enum):
    """Where processing physically occurs."""
    LOCAL = "LOCAL"
    REMOTE = "REMOTE"
    UNKNOWN = "UNKNOWN"


@dataclass
class ModelCapability:
    """Describes what a model/provider supports."""
    streaming: bool = True
    structured_output: bool = False
    tool_calling: bool = False
    context_limit: int = 4096  # tokens
    max_output_tokens: int = 2048
    verified: bool = False  # whether capabilities were probed/verified at config time
    unsupported: list[str] = field(default_factory=list)  # explicitly unsupported features


@dataclass
class ProviderConfig:
    """Configuration for a model provider."""
    provider: str  # "hermes", "ollama", "openai_compatible"
    base_url: str
    model: str
    api_key: str | None = None
    timeout_seconds: int = 120
    temperature: float | None = None
    max_tokens: int | None = None
    app_context_budget: int = 8000  # tokens reserved for context from the app
    # Optional OpenAI-compatible request fields
    extra_headers: dict[str, str] | None = None
    verify_ssl: bool = True


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass
class ChatResult:
    """Non-streaming result from a chat completion."""
    content: str
    finish_reason: str | None = "stop"
    model: str | None = None
    usage: TokenUsage | None = None
    request_id: str | None = None
    processing_location: ProcessingLocation = ProcessingLocation.UNKNOWN
    raw_response: dict[str, Any] | None = None


@dataclass
class StreamingChunk:
    """A single chunk from a streaming response."""
    content_delta: str  # new text content
    finish_reason: str | None = None
    model: str | None = None
    usage: TokenUsage | None = None
    request_id: str | None = None
    processing_location: ProcessingLocation = ProcessingLocation.UNKNOWN


@dataclass
class ProviderError(Exception):
    """Normalized error from a model provider."""
    code: str  # e.g. "rate_limit", "auth_failed", "context_overflow", "server_error"
    message: str
    status_code: int | None = None
    retry_after: float | None = None
    provider_name: str | None = None
    original_error: dict[str, Any] | None = None

    def __str__(self) -> str:
        return f"[{self.provider_name}] {self.code}: {self.message}"


class ProviderErrorType(str, Enum):
    RATE_LIMIT = "rate_limit"
    AUTH_FAILED = "auth_failed"
    CONTEXT_OVERFLOW = "context_overflow"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@runtime_checkable
class ModelProvider(Protocol):
    """Protocol that all model providers must implement."""

    config: ProviderConfig

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
    ) -> ChatResult | AsyncIterator[StreamingChunk]:
        """Send a chat completion request.

        If stream=True, returns an async iterator of StreamingChunk.
        Otherwise returns a ChatResult.
        """
        ...

    async def get_capabilities(self) -> ModelCapability:
        """Probe and return the model's capabilities."""
        ...

    def get_processing_location(self) -> ProcessingLocation:
        """Return where processing occurs (LOCAL, REMOTE, UNKNOWN)."""
        ...

    def get_model_identity(self) -> str:
        """Return the actual model name being used."""
        return self.config.model


@dataclass
class ProviderBudgets:
    """Per-request and lifetime budgets for a provider."""
    max_requests: int | None = None
    max_tokens: int | None = None
    max_retries: int = 3
    max_concurrency: int = 4
    max_steps: int = 20  # for agentic loops
