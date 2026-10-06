# AnthropicProvider against a mocked SDK client: request shape, usage, and
# mapping of SDK errors / stop reasons to ProviderError codes.

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from ai_agents.agents.business_analyst import BusinessAnalysisOutput
from ai_agents.providers import AnthropicProvider, ProviderError, ProviderNotConfiguredError
from ai_agents.task_bridge import draft_to_task_form, StepTaskDraft
from conftest import sample_ba_output, sample_task_draft


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.kwargs = response, error, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response

    create = parse


def _client(response=None, error=None):
    messages = FakeMessages(response, error)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def _response(stop_reason="end_turn", parsed=None):
    usage = SimpleNamespace(input_tokens=1200, output_tokens=800, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed, model="claude-opus-5-5", usage=usage,
                           stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
                           content=[SimpleNamespace(type="text", text="hello")])


def test_missing_api_key_raises_not_configured():
    with pytest.raises(ProviderNotConfiguredError):
        AnthropicProvider(api_key=None, model="claude-opus-5-5")


def test_structured_output_request_and_result():
    parsed = BusinessAnalysisOutput.model_validate(sample_ba_output())
    client, messages = _client(_response(parsed=parsed))
    provider = AnthropicProvider(api_key=None, model="claude-opus-5-5", effort="high", client=client)

    result = provider.structured_output(system="SYS", prompt="PROMPT", output_model=BusinessAnalysisOutput, max_tokens=16000)

    assert result.output is parsed
    assert (result.usage.input_tokens, result.usage.output_tokens) == (1200, 800)
    k = messages.kwargs
    assert k["model"] == "claude-opus-5-5" and k["system"] == "SYS" and k["max_tokens"] == 16000
    assert k["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert k["output_format"] is BusinessAnalysisOutput
    assert k["output_config"] == {"effort": "high"}
    assert k["fallbacks"] == "default" and k["betas"] == ["server-side-fallback-2026-07-01"]


def test_generate_returns_text():
    client, _ = _client(_response())
    result = AnthropicProvider(api_key=None, model="m", client=client).generate(system="s", prompt="p")
    assert result.text == "hello"


@pytest.mark.parametrize("stop_reason,code", [("refusal", "refused"), ("max_tokens", "output_truncated")])
def test_stop_reasons_map_to_errors(stop_reason, code):
    client, _ = _client(_response(stop_reason=stop_reason))
    provider = AnthropicProvider(api_key=None, model="m", client=client)
    with pytest.raises(ProviderError) as exc:
        provider.structured_output(system="s", prompt="p", output_model=BusinessAnalysisOutput)
    assert exc.value.code == code and exc.value.usage.output_tokens == 800


def test_empty_parse_is_invalid_output():
    client, _ = _client(_response(parsed=None))
    with pytest.raises(ProviderError) as exc:
        AnthropicProvider(api_key=None, model="m", client=client).structured_output(
            system="s", prompt="p", output_model=BusinessAnalysisOutput)
    assert exc.value.code == "invalid_output"


def _status_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("api error", response=httpx2.Response(status, request=request), body=None)


@pytest.mark.parametrize("error,code,retryable", [
    (_status_error(anthropic.AuthenticationError, 401), "provider_auth", False),
    (_status_error(anthropic.PermissionDeniedError, 403), "provider_permission", False),
    (_status_error(anthropic.NotFoundError, 404), "provider_config", False),
    (_status_error(anthropic.RateLimitError, 429), "rate_limited", True),
    (_status_error(anthropic.BadRequestError, 400), "provider_bad_request", False),
    (_status_error(anthropic.InternalServerError, 500), "provider_unavailable", True),
    (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x")), "timeout", True),
    (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), "provider_unreachable", True),
])
def test_sdk_errors_are_mapped(error, code, retryable):
    client, _ = _client(error=error)
    with pytest.raises(ProviderError) as exc:
        AnthropicProvider(api_key=None, model="m", client=client).structured_output(
            system="s", prompt="p", output_model=BusinessAnalysisOutput)
    assert exc.value.code == code and exc.value.retryable is retryable
    assert "api.anthropic.com" not in exc.value.user_message


def test_task_bridge_formats_and_clamps():
    form = draft_to_task_form(StepTaskDraft.model_validate(sample_task_draft(title="T" * 300, estimated_hours=0)))
    assert len(form["title"]) == 200
    assert form["estimated_hours"] == 1
    assert "Deliverables:\n- Research report\n- Clickable prototype" in form["requirements"]
    assert "Milestones:\n- Research plan - Interview guide agreed with the company" in form["requirements"]
