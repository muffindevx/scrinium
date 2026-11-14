"""What local LLMs share: messages, response, limits and the provider contract."""

import re
from dataclasses import dataclass
from typing import Any, ClassVar, Final, Literal, Protocol

from scrinium.infrastructure.local_http import ModelError, ModelStatus, Outcome

# A prompt longer than the context would lose its instructions (Ollama trims it
# silently). The context is pinned and input that does not fit with margin is rejected.
CONTEXT_TOKENS: Final = 12_288
MAX_INPUT_CHARS: Final = 32_000
MAX_OUTPUT_TOKENS: Final = 4_096
THINK_TAGS: Final = re.compile(r"<think>.*?</think>", re.S)

Role = Literal["system", "user", "assistant"]

# The system prompt is identical on every request, so the server reuses its cache
# (llama-server, and providers with prompt caching). That is why it does not carry the random
# value of the markers: it names it CLAVE (key), and the value goes at the start of the user
# message, which is what changes from one request to the next.
KEY_NAME: Final = "CLAVE"


def key_line(nonce: str) -> str:
    """First line of the user message: the value of CLAVE (the key) in this request."""
    return f"{KEY_NAME} de las marcas de esta peticion: {nonce}\n\n"


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


# Generation settings for every local model (F9): deterministic, so a batch can be repeated.
DEFAULT_TEMPERATURE: Final = 0.0
DEFAULT_SEED: Final = 1


@dataclass(frozen=True)
class Completion:
    """A completed answer, the same shape from every provider (ADR-010).

    Any other outcome is a `ModelError` with its `outcome`, never a `Completion`. `text` is the
    answer only: reasoning is dropped by the adapter. `model_id` is the model that actually
    answered. `prompt_tokens` counts all input, cached or not.
    """

    text: str
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int
    # Of the input, the tokens the server reused from its cache (it did not recompute them, and a
    # paid provider would not charge them at full price). 0 if the provider does not say.
    cached_tokens: int = 0
    # llama-server: how long it took to read the input and write the output, and of the tokens
    # speculative decoding proposed, how many it accepted. 0 if the provider does not say.
    prompt_ms: int = 0
    generation_ms: int = 0
    draft_tokens: int = 0
    draft_accepted: int = 0
    # The provider's id for this request, for support. Never content. Empty if it gives none.
    request_id: str = ""


@dataclass
class TokenUsage:
    """Tokens spent by an analysis: added batch by batch and stored in its result."""

    input: int = 0
    output: int = 0
    cached: int = 0
    calls: int = 0

    def add(self, completion: Completion) -> None:
        self.input += completion.prompt_tokens
        self.output += completion.completion_tokens
        self.cached += completion.cached_tokens
        self.calls += 1

    def as_dict(self) -> dict[str, int]:
        return {"input": self.input, "output": self.output, "cached": self.cached,
                "calls": self.calls}  # fmt: skip


class OutputBudgetExceededError(ModelError):
    """The model ran out of tokens before answering (usually, while reasoning)."""

    outcome: ClassVar[Outcome] = Outcome.LENGTH


class ModelRefusedError(ModelError):
    """The provider declined to answer (a safety filter). The batch fails like any other."""

    outcome: ClassVar[Outcome] = Outcome.REFUSED


class LLMProvider(Protocol):
    @property
    def model_id(self) -> str:
        """`provider:model`, like `llama:qwen3-4b-instruct-2507`."""

    @property
    def is_local(self) -> bool:
        """True if the text never leaves this machine. The AI policy requires it."""

    def status(self) -> ModelStatus: ...

    def complete(
        self,
        messages: list[ChatMessage],
        max_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion: ...


def provider_name(provider: LLMProvider) -> str:
    return provider.model_id.split(":", 1)[0]


def check_request(messages: list[ChatMessage], max_tokens: int) -> None:
    if not messages or not 1 <= max_tokens <= MAX_OUTPUT_TOKENS:
        raise ValueError("invalid request")
    if sum(len(message.content) for message in messages) > MAX_INPUT_CHARS:
        raise ValueError("input too long")
