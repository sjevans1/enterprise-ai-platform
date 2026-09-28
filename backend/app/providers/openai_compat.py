"""GenericOpenAICompatibleProvider: Communicates with any OpenAI-compatible API.

Uses documented OpenAI-compatible endpoints. Does not invent provider-specific
parameters. Configuration is fully driven by admin settings (base_url, model,
api_key).

Spec: "GenericOpenAICompatibleProvider must use documented compatible endpoints,
authentication and configurable model identifiers."
"""

from __future__ import annotations

from app.providers.base import ProcessingLocation, ProviderConfig
from app.providers.http_provider import HTTPModelProvider


class GenericOpenAICompatibleProvider(HTTPModelProvider):
    """Provider for any OpenAI-compatible API endpoint."""

    def __init__(self, config: ProviderConfig):
        if not config.base_url:
            raise ValueError("base_url is required for GenericOpenAICompatibleProvider")
        if not config.model:
            raise ValueError("model is required for GenericOpenAICompatibleProvider")
        super().__init__(config)

    def get_processing_location(self) -> ProcessingLocation:
        """Classify based on whether the endpoint is localhost."""
        if self._is_localhost(self.config.base_url):
            return ProcessingLocation.LOCAL
        return ProcessingLocation.REMOTE

    @staticmethod
    def _is_localhost(url: str) -> bool:
        for host in ("127.0.0.1", "localhost", "0.0.0.0", "::1"):
            if host in url:
                return True
        return False

    @classmethod
    def from_settings(cls, **overrides) -> "GenericOpenAICompatibleProvider":
        from app.core.config import settings

        config = ProviderConfig(
            provider="openai_compatible",
            base_url=settings.openai_compatible_base,
            model=settings.openai_compatible_model,
            api_key=settings.openai_compatible_key.get_secret_value() or None,
            timeout_seconds=settings.request_timeout_seconds,
            temperature=settings.default_temperature,
            max_tokens=settings.default_max_tokens,
            app_context_budget=8000,
        )
        for k, v in overrides.items():
            if hasattr(config, k):
                setattr(config, k, v)
        return cls(config)
