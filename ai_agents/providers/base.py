# Provider-neutral interface for LLM calls.
# Agents and the execution service only depend on this module, so swapping or
# adding a provider means writing one new AIProvider subclass.

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel

OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    web_search_requests: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            web_search_requests=self.web_search_requests + other.web_search_requests,
        )


@dataclass
class Source:
    # A web page the provider actually retrieved during research
    url: str
    title: str = ""
    page_age: Optional[str] = None


@dataclass
class TextResult:
    text: str
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)


@dataclass
class StructuredResult(Generic[OutputT]):
    output: OutputT
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)


@dataclass
class ResearchResult:
    # Free-text research notes plus the sources retrieved while producing them
    text: str
    sources: List[Source]
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)


class ProviderError(Exception):
    # Raised for any provider failure. user_message is safe to show to a company;
    # str(self) may contain technical detail and is only logged.
    def __init__(self, code: str, user_message: str, *, detail: Optional[str] = None,
                 retryable: bool = False, usage: Optional[Usage] = None):
        super().__init__(detail or user_message)
        self.code = code
        self.user_message = user_message
        self.retryable = retryable
        # Tokens may still be billed on refusals or truncated output
        self.usage = usage


class ProviderNotConfiguredError(ProviderError):
    def __init__(self, detail: str):
        super().__init__(
            "provider_not_configured",
            "The AI service is not configured on this STEP server. Please contact the STEP administrator.",
            detail=detail,
        )


class AIProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, *, system: str, prompt: str, max_tokens: int = 4000,
                 effort: Optional[str] = None) -> TextResult:
        """Free-text completion."""

    def research(self, *, system: str, prompt: str, max_searches: int = 8, max_tokens: int = 16000,
                 effort: Optional[str] = None) -> ResearchResult:
        """Research a question using live web search. Optional capability."""
        raise ProviderError(
            "capability_unavailable",
            "This agent needs live web research, which the configured AI provider does not support.",
            detail=f"{self.name} does not implement research()",
        )

    @abstractmethod
    def structured_output(self, *, system: str, prompt: str, output_model: type[OutputT],
                          max_tokens: int = 16000, effort: Optional[str] = None) -> StructuredResult[OutputT]:
        """Completion constrained to output_model's schema and validated into an instance of it."""
