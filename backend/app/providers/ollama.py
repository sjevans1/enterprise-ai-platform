"""OllamaProvider: Direct communication with a local Ollama server.

Uses the OpenAI-compatible API exposed by Ollama.
Default same-host base: http://127.0.0.1:11434/v1

Security: Ollama local mode uses locally available model weights.
Cloud features must be disabled (OLLAMA_OFFLINE=true or equivalent).
"""

from __future__ import annotations

from app.providers.base import ProcessingLocation, ProviderConfig
from app.providers.http_provider import HTTPModelProvider


class OllamaProvider(HTTPModelProvider):
    """Provider that communicates with a local Ollama server."""

    DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"

    def __init__(self, config: ProviderConfig):
        # Ollama API uses no auth by default
        if config.api_key is None:
            config.api_key = "ollama"  # Ollama accepts any string as key
        super().__init__(config)

    def get_processing_location(self) -> ProcessingLocation:
        """Ollama on a local machine = LOCAL."""
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
    def from_settings(cls, **overrides) -> "OllamaProvider":
        from app.core.config import settings

        config = ProviderConfig(
            provider="ollama",
            base_url=settings.ollama_api_base,
            model=settings.ollama_model,
            api_key=None,  # Ollama doesn't require auth
            timeout_seconds=settings.request_timeout_seconds,
            temperature=settings.default_temperature,
            max_tokens=settings.default_max_tokens,
            app_context_budget=8000,
        )
        for k, v in overrides.items():
            if hasattr(config, k):
                setattr(config, k, v)
        return cls(config)
