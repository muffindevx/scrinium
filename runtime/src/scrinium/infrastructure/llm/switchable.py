"""The local chat model, which the first-run choice can point at another loopback server.

ADR-013: the local model is an endpoint, not a component. Scrinium starts with its managed
llama-server (or `SCRINIUM_MODEL_PROVIDER=ollama`); choosing "a local server" swaps it for any
OpenAI-compatible server on this machine, without a restart. Every target is local: the
adapters only accept loopback addresses.
"""

import threading
from typing import Any

from scrinium.infrastructure.llm.base import ChatMessage, Completion, LLMProvider
from scrinium.infrastructure.llm.openai_compatible import OpenAICompatibleChat
from scrinium.infrastructure.local_http import ModelStatus

LOCAL_SERVER_PROVIDER = "local"


def local_server_chat(url: str, model: str) -> OpenAICompatibleChat:
    """A server Scrinium does not manage (Ollama, vLLM, another llama-server): no llama-server
    extensions in the request, and no prompt-cache slot."""
    return OpenAICompatibleChat(url, model, provider=LOCAL_SERVER_PROVIDER, llama_extras=False)


class SwitchableChat:
    def __init__(self, default: LLMProvider) -> None:
        if not default.is_local:
            raise ValueError("the local chat model must be local")
        self._default = default
        self._current = default
        self._lock = threading.Lock()

    def use(self, provider: LLMProvider | None) -> None:
        """`provider`, or back to the default with None. Only local providers."""
        if provider is not None and not provider.is_local:
            raise ValueError("the local chat model must be local")
        with self._lock:
            self._current = provider or self._default

    @property
    def current(self) -> LLMProvider:
        return self._current

    @property
    def model_id(self) -> str:
        return self._current.model_id

    @property
    def is_local(self) -> bool:
        return self._current.is_local

    def status(self) -> ModelStatus:
        return self._current.status()

    def complete(
        self,
        messages: list[ChatMessage],
        max_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        return self._current.complete(messages, max_tokens, json_schema)
