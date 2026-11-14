"""Local LLM through the OpenAI-compatible API: llama-server's, managed by Scrinium.

If the model reasons, llama-server puts the reasoning in `reasoning_content`, which is discarded
unread: it is never returned, stored or logged. Any `<think>` tags left in
the text are removed too.
"""

import threading
import time
from collections.abc import Callable
from typing import Any, Final

from scrinium.infrastructure.llm.base import (
    DEFAULT_SEED,
    DEFAULT_TEMPERATURE,
    THINK_TAGS,
    ChatMessage,
    Completion,
    ModelRefusedError,
    OutputBudgetExceededError,
    check_request,
)
from scrinium.infrastructure.local_http import LocalHttpClient, ModelError, ModelStatus

_TIMEOUT_SECONDS: Final = 600
_STATUS_TIMEOUT_SECONDS: Final = 3
# Always the same slot (llama-server has one, `parallel = 1`): its cache keeps the start
# of the previous prompt, and the next one that starts the same only reads what is new.
_SLOT: Final = 0


def openai_model_status(client: LocalHttpClient | None, model: str) -> ModelStatus:
    if client is None:
        return ModelStatus.UNAVAILABLE
    try:
        listing = client.request("/v1/models", None, _STATUS_TIMEOUT_SECONDS)
    except ModelError:
        return ModelStatus.UNAVAILABLE
    entries = listing.get("data") if isinstance(listing, dict) else None
    names = {entry.get("id") for entry in entries or [] if isinstance(entry, dict)}
    return ModelStatus.READY if model in names else ModelStatus.MODEL_MISSING


def _cached_tokens(result: dict[str, Any], usage: dict[str, Any]) -> int:
    """OpenAI and llama-server: `usage.prompt_tokens_details.cached_tokens`. Versions of
    llama-server that do not give it report `timings.cache_n`."""
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and isinstance(details.get("cached_tokens"), int):
        return int(details["cached_tokens"])
    timings = result.get("timings")
    if isinstance(timings, dict) and isinstance(timings.get("cache_n"), int):
        return int(timings["cache_n"])
    return 0


def _request_id(result: dict[str, Any]) -> str:
    """The provider's `id` for the request (`chatcmpl-…`), for support; never content."""
    value = result.get("id")
    return value[:200] if isinstance(value, str) else ""


def _timings(result: dict[str, Any]) -> dict[str, int]:
    """llama-server only: `timings` separates reading the input from writing the output. Without
    it, tokens per second mix both."""
    timings = result.get("timings")
    if not isinstance(timings, dict):
        return {}
    fields = {"prompt_ms": "prompt_ms", "predicted_ms": "generation_ms",
              "draft_n": "draft_tokens", "draft_n_accepted": "draft_accepted"}  # fmt: skip
    return {
        name: int(timings[key])
        for key, name in fields.items()
        if isinstance(timings.get(key), int | float)
    }


def json_schema_format(json_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {"name": "respuesta", "schema": json_schema, "strict": True},
    }


def parse_chat_completion(result: Any, model_id: str, started: float) -> Completion:
    """`/v1/chat/completions` answer to the common contract (ADR-010): local and remote alike."""
    choices = result.get("choices") if isinstance(result, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else None
    if not isinstance(choice, dict):
        raise ModelError
    # Cut by length, the JSON is incomplete even if there is text: always a failure, with its
    # own reason, so the batch is retried smaller (F9).
    finish = choice.get("finish_reason")
    if finish == "length":
        raise OutputBudgetExceededError
    # A provider's content filter declined: a refused batch, never retried elsewhere (ADR-010).
    if finish == "content_filter":
        raise ModelRefusedError
    message = choice.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ModelError
    # Reasoning (`reasoning_content`) is never read; think tags left in the text are removed.
    text = THINK_TAGS.sub("", message["content"]).strip()
    if not text:
        raise ModelError
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    return Completion(
        cached_tokens=_cached_tokens(result, usage),
        text=text,
        model_id=model_id,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        duration_ms=int((time.perf_counter() - started) * 1000),
        request_id=_request_id(result),
        **_timings(result),
    )


class OpenAICompatibleChat:
    def __init__(
        self,
        base_url: str | None,
        model: str,
        api_key: str | None = None,
        provider: str = "llama",
        before_request: Callable[[], None] | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        seed: int | None = DEFAULT_SEED,
        slot: int = _SLOT,
    ) -> None:
        """Without `base_url` (llama-server could not start), the model shows as unavailable.

        `before_request` is called before each generation: with llama-server, `ChatFirst`.
        Temperature 0 and a fixed seed by default (F9): the same input gives the same rules, so
        a batch can be repeated and two runs compared. At 0.2 the output varied by 20 %."""
        self._client = LocalHttpClient(base_url, api_key) if base_url is not None else None
        self._model = model
        self._provider = provider
        self._before_request = before_request
        self._temperature = temperature
        # llama-server slot: each client its own, so no other client overwrites its prompt cache
        # (S1, parallel batches).
        self._slot = slot
        self._seed = seed
        # One generation at a time: the GPU is shared, and two at once are just slower.
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return f"{self._provider}:{self._model}"

    @property
    def is_local(self) -> bool:
        return True  # LocalHttpClient only accepts loopback

    def status(self) -> ModelStatus:
        return openai_model_status(self._client, self._model)

    def complete(
        self,
        messages: list[ChatMessage],
        max_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        check_request(messages, max_tokens)
        if self._client is None:
            raise ModelError
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": self._temperature,
            "cache_prompt": True,
            "id_slot": self._slot,
        }
        if self._seed is not None:
            body["seed"] = self._seed
        if json_schema is not None:
            body["response_format"] = json_schema_format(json_schema)
        started = time.perf_counter()
        with self._lock:
            if self._before_request is not None:
                self._before_request()
            result = self._client.request("/v1/chat/completions", body, _TIMEOUT_SECONDS)
        return parse_chat_completion(result, self.model_id, started)
