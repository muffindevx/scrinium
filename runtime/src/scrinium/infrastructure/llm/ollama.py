"""Local LLM with Ollama (ADR-004), the alternative to llama.cpp.

Some models (like `qwen3:4b`) always reason: with `think: false` they mix the reasoning
into the answer. Those are asked for `think: true` and the reasoning is discarded: it is never
returned, stored or logged, and it uses up token budget. Models without that
capability are not sent `think`: Ollama would reject it.
"""

import threading
import time
from typing import Any, Final

from scrinium.infrastructure.llm.base import (
    CONTEXT_TOKENS,
    DEFAULT_SEED,
    DEFAULT_TEMPERATURE,
    THINK_TAGS,
    ChatMessage,
    Completion,
    OutputBudgetExceededError,
    check_request,
)
from scrinium.infrastructure.local_http import ModelError, ModelStatus
from scrinium.infrastructure.ollama_client import OllamaClient

_TIMEOUT_SECONDS: Final = 600


class OllamaChat:
    def __init__(self, base_url: str, model: str) -> None:
        self._client = OllamaClient(base_url)
        self._model = model
        # One generation at a time: the GPU is shared, and two at once are just slower.
        self._lock = threading.Lock()
        self._thinks: bool | None = None

    def _supports_thinking(self) -> bool:
        """Ollama is asked once about the model's capabilities."""
        if self._thinks is None:
            info = self._client.request("/api/show", {"model": self._model}, 30)
            capabilities = info.get("capabilities", []) if isinstance(info, dict) else []
            self._thinks = "thinking" in capabilities
        return self._thinks

    @property
    def model_id(self) -> str:
        return f"ollama:{self._model}"

    @property
    def is_local(self) -> bool:
        return True  # OllamaClient only accepts loopback

    def status(self) -> ModelStatus:
        return self._client.model_status(self._model)

    def complete(
        self,
        messages: list[ChatMessage],
        max_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        check_request(messages, max_tokens)
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {
                "num_ctx": CONTEXT_TOKENS,
                "num_predict": max_tokens,
                "temperature": DEFAULT_TEMPERATURE,
                "seed": DEFAULT_SEED,
            },
        }
        if json_schema is not None:
            body["format"] = json_schema
        started = time.perf_counter()
        with self._lock:
            if self._supports_thinking():
                body["think"] = True
            result = self._client.request("/api/chat", body, _TIMEOUT_SECONDS)
        message = result.get("message") if isinstance(result, dict) else None
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ModelError
        # Cut by length, the JSON is incomplete even with text: a failure, like every provider.
        if result.get("done_reason") == "length":
            raise OutputBudgetExceededError
        text = THINK_TAGS.sub("", message["content"]).strip()
        if not text:
            raise ModelError
        return Completion(
            text=text,
            model_id=self.model_id,
            prompt_tokens=int(result.get("prompt_eval_count") or 0),
            completion_tokens=int(result.get("eval_count") or 0),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
