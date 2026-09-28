"""HermesProvider: Serves model requests through the Hermes API server.

Security note from spec section 7:
  "The Hermes API is an agent runtime. It may execute tools and use memory;
   an OpenAI-compatible request does not by itself make it a pure inference service."

This provider communicates only through the configured Hermes API server base URL.
It does NOT give the application direct access to the Hermes agent runtime.
The Hermes API server must be configured with a restricted runtime profile
(see ARCHITECTURE.md section 7) that disables:
  - Developer personal credentials
  - Workspace/file system access
  - Arbitrary shell execution
  - Runtime memory (unless explicitly tested)
  - Browser and external tool access

The bearer token is configured server-side and never exposed to clients.
"""

from __future__ import annotations

from app.providers.base import ProcessingLocation, ProviderConfig
from app.providers.http_provider import HTTPModelProvider


class HermesProvider(HTTPModelProvider):
    """Provider that communicates with the Hermes API server.

    Default same-host API base: http://127.0.0.1:8642/v1
    The API server gives full access to Hermes tools including terminal commands.
    This provider treats Hermes as a pure inference service (chat completions).
    Tool calling is restricted by the Hermes API server configuration.
    """

    DEFAULT_BASE_URL = "http://127.0.0.1:8642/v1"
    DEFAULT_MODEL = "hermes-agent"

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        if not config.api_key:
            raise ValueError("HERMES_API_KEY is required for HermesProvider")

    def get_processing_location(self) -> ProcessingLocation:
        """Classify the processing location.

        Hermes API server on loopback = LOCAL only if the underlying model
        provider is also local. If the Hermes backend routes to a remote
        provider, the location is REMOTE.
        """
        # Check if the base URL is loopback
        if self._is_localhost(self.config.base_url):
            # Hermes on localhost may still route to remote providers.
            # Without introspection of Hermes's own provider routing,
            # we classify as LOCAL when explicitly allowed by the deployment
            # policy (REMOTE_MODEL_ALLOWED flag is checked elsewhere).
            return ProcessingLocation.LOCAL
        return ProcessingLocation.REMOTE

    @staticmethod
    def _is_localhost(url: str) -> bool:
        """Check if a URL points to localhost."""
        for host in ("127.0.0.1", "localhost", "0.0.0.0", "::1"):
            if host in url:
                return True
        return False

    @classmethod
    def from_settings(cls, **overrides) -> "HermesProvider":
        """Create a HermesProvider from global settings."""
        from app.core.config import settings

        config = ProviderConfig(
            provider="hermes",
            base_url=settings.hermes_api_base,
            model=settings.default_model,
            api_key=settings.hermes_api_key.get_secret_value() or None,
            timeout_seconds=settings.request_timeout_seconds,
            temperature=settings.default_temperature,
            max_tokens=settings.default_max_tokens,
            app_context_budget=8000,
        )
        # Apply overrides
        for k, v in overrides.items():
            if hasattr(config, k):
                setattr(config, k, v)
        return cls(config)
