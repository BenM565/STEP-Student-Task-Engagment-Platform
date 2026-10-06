# Anthropic (Claude) implementation of AIProvider.
# Credentials come from ANTHROPIC_API_KEY on the server only; nothing here is
# ever rendered into templates or sent to the browser.

import logging
from typing import Optional

from .base import (
    AIProvider,
    ProviderError,
    ProviderNotConfiguredError,
    StructuredResult,
    TextResult,
    Usage,
)

log = logging.getLogger(__name__)

# Server-side refusal fallback: if the primary model declines a request, the API
# re-runs it on a fallback model chosen by refusal category, inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, *, api_key: Optional[str], model: str, effort: Optional[str] = None,
                 timeout_seconds: float = 300.0, max_retries: int = 2, client=None):
        self.model = model
        self.default_effort = effort
        if client is not None:
            # Injected client (tests)
            self._client = client
            return
        if not api_key:
            raise ProviderNotConfiguredError("ANTHROPIC_API_KEY is not set")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
            raise ProviderNotConfiguredError("The 'anthropic' package is not installed") from exc
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds, max_retries=max_retries)

    # Shared request options
    def _common(self, system: str, prompt: str, max_tokens: int, effort: Optional[str]) -> dict:
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
        effort = effort or self.default_effort
        if effort:
            kwargs["output_config"] = {"effort": effort}
        return kwargs

    def generate(self, *, system, prompt, max_tokens=4000, effort=None) -> TextResult:
        response = self._call(self._client.beta.messages.create, **self._common(system, prompt, max_tokens, effort))
        usage = _usage(response)
        self._check_stop_reason(response, usage)
        text = "".join(b.text for b in response.content if b.type == "text")
        return TextResult(text=text, model=response.model, provider=self.name, usage=usage)

    def structured_output(self, *, system, prompt, output_model, max_tokens=16000, effort=None) -> StructuredResult:
        response = self._call(
            self._client.beta.messages.parse,
            output_format=output_model,
            **self._common(system, prompt, max_tokens, effort),
        )
        usage = _usage(response)
        self._check_stop_reason(response, usage)
        parsed = response.parsed_output
        if parsed is None:
            raise ProviderError(
                "invalid_output",
                "The AI returned a result in an unexpected format. Please run the agent again.",
                detail=f"parsed_output was empty (stop_reason={response.stop_reason})",
                retryable=True,
                usage=usage,
            )
        return StructuredResult(output=parsed, model=response.model, provider=self.name, usage=usage)

    def _check_stop_reason(self, response, usage: Usage) -> None:
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise ProviderError(
                "refused",
                "The AI declined to process this request. Please review the input and try rephrasing it.",
                detail=f"refusal category={category}",
                usage=usage,
            )
        if response.stop_reason == "max_tokens":
            raise ProviderError(
                "output_truncated",
                "The AI's answer was too long and was cut off. Try narrowing the problem or attaching fewer documents.",
                detail="stop_reason=max_tokens",
                usage=usage,
            )

    def _call(self, fn, **kwargs):
        import anthropic

        try:
            return fn(**kwargs)
        # Most specific first: APITimeoutError subclasses APIConnectionError
        except anthropic.AuthenticationError as exc:
            raise ProviderError("provider_auth", "The AI service rejected STEP's credentials. Please contact the STEP administrator.",
                                detail=str(exc)) from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderError("provider_permission", "STEP's AI account is not permitted to make this request. Please contact the STEP administrator.",
                                detail=str(exc)) from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError("provider_config", "The configured AI model is not available. Please contact the STEP administrator.",
                                detail=str(exc)) from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("rate_limited", "The AI service is busy right now. Please wait a minute and run the agent again.",
                                detail=str(exc), retryable=True) from exc
        except anthropic.BadRequestError as exc:
            raise ProviderError("provider_bad_request", "The AI service could not process this request. If you attached large documents, try fewer or shorter ones.",
                                detail=str(exc)) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError("provider_unavailable", "The AI service had a temporary problem. Please try again shortly.",
                                detail=f"{exc.status_code}: {exc}", retryable=exc.status_code >= 500) from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderError("timeout", "The AI service took too long to respond. Please try again.",
                                detail=str(exc), retryable=True) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("provider_unreachable", "STEP could not reach the AI service. Please try again shortly.",
                                detail=str(exc), retryable=True) from exc


def _usage(response) -> Usage:
    u = getattr(response, "usage", None)
    if u is None:
        return Usage()
    cached = (getattr(u, "cache_read_input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0)
    return Usage(input_tokens=(u.input_tokens or 0) + cached, output_tokens=u.output_tokens or 0)
