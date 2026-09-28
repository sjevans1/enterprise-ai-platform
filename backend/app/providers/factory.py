"""Provider factory: creates the correct ModelProvider based on configuration.

Spec section 6: "Change supported model providers through configuration."
"Provider switching must preserve conversations, documents, permissions and
the knowledge index. It must not require source-code changes."
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.providers.base import ModelProvider, ProcessingLocation, ProviderConfig
from app.providers.hermes import HermesProvider
from app.providers.ollama import OllamaProvider
from app.providers.openai_compat import GenericOpenAICompatibleProvider


# Registry of available providers
_PROVIDER_REGISTRY: dict[str, type] = {
    "hermes": HermesProvider,
    "ollama": OllamaProvider,
    "openai_compatible": GenericOpenAICompatibleProvider,
}


def register_provider(name: str, provider_cls: type) -> None:
    """Register a custom provider class."""
    _PROVIDER_REGISTRY[name] = provider_cls


def create_provider(provider_name: str, config: ProviderConfig | None = None) -> ModelProvider:
    """Create a model provider instance by name.

    Args:
        provider_name: One of "hermes", "ollama", "openai_compatible".
        config: Optional explicit config. If None, built from settings.

    Raises:
        ValueError: If the provider is not registered or remote models
            are disabled by deployment policy.
    """
    provider_cls = _PROVIDER_REGISTRY.get(provider_name)
    if provider_cls is None:
        raise ValueError(f"Unknown provider: {provider_name}. Available: {list(_PROVIDER_REGISTRY.keys())}")

    if config is None:
        if provider_name == "hermes":
            config = ProviderConfig(
                provider="hermes",
                base_url=settings.hermes_api_base,
                model=settings.default_model,
                api_key=settings.hermes_api_key.get_secret_value() or None,
                timeout_seconds=settings.request_timeout_seconds,
                temperature=settings.default_temperature,
                max_tokens=settings.default_max_tokens,
            )
        elif provider_name == "ollama":
            config = ProviderConfig(
                provider="ollama",
                base_url=settings.ollama_api_base,
                model=settings.ollama_model,
                api_key=None,
                timeout_seconds=settings.request_timeout_seconds,
                temperature=settings.default_temperature,
                max_tokens=settings.default_max_tokens,
            )
        elif provider_name == "openai_compatible":
            config = ProviderConfig(
                provider="openai_compatible",
                base_url=settings.openai_compatible_base,
                model=settings.openai_compatible_model,
                api_key=settings.openai_compatible_key.get_secret_value() or None,
                timeout_seconds=settings.request_timeout_seconds,
                temperature=settings.default_temperature,
                max_tokens=settings.default_max_tokens,
            )
        else:
            raise ValueError(f"Cannot build default config for provider: {provider_name}")

    provider = provider_cls(config)

    # Privacy enforcement: reject remote/unknown inference in local-only mode
    if not settings.remote_model_allowed:
        location = provider.get_processing_location()
        if location != ProcessingLocation.LOCAL:
            raise ValueError(
                f"Remote model provider '{provider_name}' is not allowed "
                f"(REMOTE_MODEL_ALLOWED=false). Provider location: {location.value}"
            )

    return provider


def get_default_provider() -> ModelProvider:
    """Create the provider configured as the default in settings."""
    return create_provider(settings.default_provider)


def list_available_providers() -> dict[str, dict[str, Any]]:
    """Return metadata about all registered providers."""
    result: dict[str, dict[str, Any]] = {}
    for name, cls in _PROVIDER_REGISTRY.items():
        result[name] = {
            "module": cls.__module__,
            "class": cls.__name__,
            "is_registered": True,
        }
    return result
