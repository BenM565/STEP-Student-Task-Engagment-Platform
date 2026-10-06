# Provider factory. AI_PROVIDER selects the implementation; tests can inject an
# instance via app.config["AI_PROVIDER_INSTANCE"].

from flask import current_app

from .anthropic_provider import AnthropicProvider
from .base import (
    AIProvider,
    ProviderError,
    ProviderNotConfiguredError,
    StructuredResult,
    TextResult,
    Usage,
)


def _build_anthropic(config) -> AIProvider:
    return AnthropicProvider(
        api_key=config.get("ANTHROPIC_API_KEY"),
        model=config["AI_MODEL"],
        effort=config.get("AI_EFFORT") or None,
        timeout_seconds=float(config["AI_TIMEOUT_SECONDS"]),
    )


# Register new providers here: name -> factory(config)
PROVIDER_FACTORIES = {
    "anthropic": _build_anthropic,
}


def get_provider() -> AIProvider:
    injected = current_app.config.get("AI_PROVIDER_INSTANCE")
    if injected is not None:
        return injected
    name = current_app.config["AI_PROVIDER"]
    factory = PROVIDER_FACTORIES.get(name)
    if factory is None:
        raise ProviderNotConfiguredError(f"Unknown AI_PROVIDER '{name}'")
    return factory(current_app.config)


__all__ = [
    "AIProvider",
    "AnthropicProvider",
    "ProviderError",
    "ProviderNotConfiguredError",
    "StructuredResult",
    "TextResult",
    "Usage",
    "get_provider",
    "PROVIDER_FACTORIES",
]
