"""
Common HTTP-based provider implementation.

Both HermesProvider and OllamaProvider use the OpenAI Python SDK as the
transport layer. This shared implementation normalises responses, errors,
streaming, and token usage across all three provider types.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from openai import (
    AsyncOpenAI,
    AsyncStream,
    AuthenticationError,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    BadRequestError,
    RateLimitError,
)
from openai.types.chat import ChatCompletionChunk, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice, CompletionUsage

from app.providers.base import (
    ChatResult,
    ModelCapability,
    ProcessingLocation,
    ProviderConfig,
    ProviderError,
    ProviderErrorType,
    StreamingChunk,
    TokenUsage,
)


class HTTPModelProvider:
    """Base class implementing the OpenAI-compatible chat protocol.

    Concrete providers (Hermes, Ollama, generic OpenAI) inherit this
    and set the appropriate default config and processing location.
    """

    config: ProviderConfig
    _client: AsyncOpenAI | None = None
    _capabilities: ModelCapability | None = None

    def __init__(self, config: ProviderConfig):
        self.config = config

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            import httpx
            client_kwargs: dict[str, Any] = {
                "base_url": self.config.base_url,
                "api_key": self.config.api_key,
                "timeout": self.config.timeout_seconds,
                "max_retries": 0,
            }
            if not self.config.verify_ssl:
                client_kwargs["http_client"] = httpx.AsyncClient(verify=False)
            self._client = AsyncOpenAI(**client_kwargs)
        return self._client

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
    ) -> ChatResult | Any:
        """Non-streaming: returns ChatResult. Streaming: returns async iterator."""
        if stream:
            return self._stream(messages, temperature, max_tokens, tools, response_format, timeout_seconds)
        return await self._chat(messages, temperature, max_tokens, tools, response_format, timeout_seconds)

    async def _chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None,
        max_tokens: int | None,
        tools: list[dict[str, Any]] | None,
        response_format: dict[str, Any] | None,
        timeout_seconds: int | None,
    ) -> ChatResult:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format

        effective_timeout = timeout_seconds or self.config.timeout_seconds
        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(**kwargs),
                timeout=effective_timeout,
            )
            chunk = response.choices[0]
            content = chunk.message.content or ""
            finish_reason = chunk.finish_reason

            usage = None
            if response.usage:
                usage = TokenUsage(
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    total_tokens=response.usage.total_tokens,
                )

            return ChatResult(
                content=content,
                finish_reason=finish_reason,
                model=response.model,
                usage=usage,
                request_id=response.id,
                processing_location=self.get_processing_location(),
                raw_response=response.model_dump() if hasattr(response, "model_dump") else None,
            )
        except RateLimitError as e:
            retry_after = None
            if e.response and "headers" in dir(e.response):
                retry_after = float(e.response.headers.get("retry-after", 0))
            raise ProviderError(
                code=ProviderErrorType.RATE_LIMIT.value,
                message=str(e),
                status_code=429,
                retry_after=retry_after,
                provider_name=self.config.provider,
            ) from e
        except AuthenticationError as e:
            raise ProviderError(
                code=ProviderErrorType.AUTH_FAILED.value,
                message=str(e),
                status_code=401,
                provider_name=self.config.provider,
            ) from e
        except BadRequestError as e:
            status_code = e.status_code if e.status_code else 400
            raise ProviderError(
                code=ProviderErrorType.UNKNOWN.value,
                message=str(e),
                status_code=status_code,
                provider_name=self.config.provider,
            ) from e
        except APIConnectionError as e:
            raise ProviderError(
                code=ProviderErrorType.SERVER_ERROR.value,
                message=str(e),
                status_code=502,
                provider_name=self.config.provider,
            ) from e
        except APITimeoutError as e:
            raise ProviderError(
                code=ProviderErrorType.TIMEOUT.value,
                message=str(e),
                provider_name=self.config.provider,
            ) from e
        except asyncio.TimeoutError:
            raise ProviderError(
                code=ProviderErrorType.TIMEOUT.value,
                message=f"Request timed out after {effective_timeout}s",
                provider_name=self.config.provider,
            )
        except APIStatusError as e:
            raise ProviderError(
                code=ProviderErrorType.UNKNOWN.value,
                message=str(e),
                status_code=e.status_code,
                provider_name=self.config.provider,
            ) from e
        except Exception as e:
            raise ProviderError(
                code=ProviderErrorType.UNKNOWN.value,
                message=str(e),
                provider_name=self.config.provider,
            ) from e

    async def _stream(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None,
        max_tokens: int | None,
        tools: list[dict[str, Any]] | None,
        response_format: dict[str, Any] | None,
        timeout_seconds: int | None,
    ) -> AsyncIterator[StreamingChunk]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format

        effective_timeout = timeout_seconds or self.config.timeout_seconds
        try:
            stream = await asyncio.wait_for(
                self.client.chat.completions.create(**kwargs),
                timeout=effective_timeout,
            )
            assert isinstance(stream, AsyncStream)

            async for raw_chunk in stream:
                if not raw_chunk.choices:
                    continue
                choice = raw_chunk.choices[0]
                content_delta = ""
                if choice.delta and choice.delta.content:
                    content_delta = choice.delta.content
                yield StreamingChunk(
                    content_delta=content_delta,
                    finish_reason=choice.finish_reason,
                    model=raw_chunk.model,
                    request_id=raw_chunk.id,
                    processing_location=self.get_processing_location(),
                )
        except RateLimitError as e:
            raise ProviderError(
                code=ProviderErrorType.RATE_LIMIT.value,
                message=str(e),
                status_code=429,
                provider_name=self.config.provider,
            ) from e
        except AuthenticationError as e:
            raise ProviderError(
                code=ProviderErrorType.AUTH_FAILED.value,
                message=str(e),
                status_code=401,
                provider_name=self.config.provider,
            ) from e
        except APIConnectionError as e:
            raise ProviderError(
                code=ProviderErrorType.SERVER_ERROR.value,
                message=str(e),
                status_code=502,
                provider_name=self.config.provider,
            ) from e
        except APITimeoutError as e:
            raise ProviderError(
                code=ProviderErrorType.TIMEOUT.value,
                message=str(e),
                provider_name=self.config.provider,
            ) from e
        except asyncio.TimeoutError:
            raise ProviderError(
                code=ProviderErrorType.TIMEOUT.value,
                message=f"Request timed out after {effective_timeout}s",
                provider_name=self.config.provider,
            )
        except Exception as e:
            raise ProviderError(
                code=ProviderErrorType.UNKNOWN.value,
                message=str(e),
                provider_name=self.config.provider,
            ) from e

    async def get_capabilities(self) -> ModelCapability:
        """Probe provider capabilities at configuration time."""
        if self._capabilities is not None:
            return self._capabilities

        caps = ModelCapability(
            streaming=True,
            structured_output=False,
            tool_calling=False,
            context_limit=4096,
            max_output_tokens=self.config.max_tokens or 2048,
            verified=False,
        )

        unsupported: list[str] = []

        # Probe via /v1/models endpoint
        try:
            models_resp = await asyncio.wait_for(
                self.client.models.list(),
                timeout=10,
            )
            for m in models_resp.data:
                if m.id == self.config.model:
                    caps.verified = True
                    # Use model's context window if available
                    if hasattr(m, "context_window"):
                        caps.context_limit = m.context_window
                    break
        except Exception:
            # Fallback: try a minimal non-streaming request
            try:
                resp = await self.client.chat.completions.create(
                    model=self.config.model,
                    messages=[{"role": "user", "content": "hello"}],
                    max_tokens=1,
                )
                caps.verified = True
            except Exception:
                caps.verified = False
                unsupported.append("all_chat")

        # Check tool calling support
        try:
            resp = await self.client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "user", "content": "name a color"}],
                max_tokens=10,
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "test_tool",
                        "description": "Test function",
                        "parameters": {"type": "object", "properties": {}},
                    }
                }],
            )
            caps.tool_calling = bool(getattr(resp.choices[0].message, "tool_calls", None))
        except Exception:
            unsupported.append("tool_calling")

        # Check structured output (response_format with JSON schema)
        try:
            resp = await self.client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "user", "content": "respond with JSON: {\"key\": \"value\"}"}],
                max_tokens=10,
                response_format={"type": "json_object"},
            )
            caps.structured_output = True
        except Exception:
            unsupported.append("structured_output")

        caps.unsupported = unsupported
        self._capabilities = caps
        return caps

    def get_processing_location(self) -> ProcessingLocation:
        """Override in subclasses to classify the processing location."""
        raise NotImplementedError("Subclasses must implement get_processing_location")

    def get_model_identity(self) -> str:
        return self.config.model
