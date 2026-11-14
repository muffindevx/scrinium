"""A remote model through an OpenAI-compatible API: DeepSeek, GLM and others (ADR-010).

Same request and the same contract as llama-server (`openai_compatible.py`), without what only
llama-server understands (prompt slot, cache flag, seed). The key is read for each request and
sent only to the host it was registered for.

Some providers accept only "JSON mode" (`json_object`), not a JSON schema. If the provider
rejects the schema (HTTP 400), this adapter asks again in JSON mode, on the same model, and
keeps doing so: the task still validates every answer against its schema and drops what does
not fit. It never moves to another model or provider.
"""

import threading
import time
from collections.abc import Callable
from typing import Any, Final

from scrinium.infrastructure.llm.base import ChatMessage, Completion, check_request
from scrinium.infrastructure.llm.openai_compatible import json_schema_format, parse_chat_completion
from scrinium.infrastructure.local_http import ModelError, ModelStatus
from scrinium.infrastructure.remote_http import (
    ProviderAuthError,
    ProviderRequestError,
    RemoteHttpsClient,
)

_TIMEOUT_SECONDS: Final = 300
_STATUS_TIMEOUT_SECONDS: Final = 15
_STATUS_PROMPT: Final = [ChatMessage("user", "Responde solo con la palabra: listo")]


class RemoteOpenAIChat:
    def __init__(
        self,
        provider: str,
        base_url: str,
        model: str,
        api_key: Callable[[], str],
        client: RemoteHttpsClient | None = None,
    ) -> None:
        """`api_key` is called for every request: with the keyring, `providers.provider_key`."""
        self._provider = provider
        self._model = model
        self._client = client or RemoteHttpsClient(
            base_url, lambda: {"Authorization": f"Bearer {api_key()}"}
        )
        self._json_mode_only = False
        # One request at a time in the first version (ADR-010): batches stay comparable.
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return f"{self._provider}:{self._model}"

    @property
    def is_local(self) -> bool:
        return False

    def status(self) -> ModelStatus:
        """No request: asking a paid provider every few seconds would spend tokens. Whether it
        answers is known from `check()` ("Probar conexión") and from each task's batches."""
        return ModelStatus.READY

    def check(self) -> ModelStatus:
        """A one-word request: whether the host, the key and the model answer."""
        try:
            self.complete(_STATUS_PROMPT, 16)
        except (ProviderAuthError, ProviderRequestError):
            return ModelStatus.MODEL_MISSING
        except ModelError:
            return ModelStatus.UNAVAILABLE
        return ModelStatus.READY

    def _body(
        self, messages: list[ChatMessage], max_tokens: int, json_schema: dict[str, Any] | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        if json_schema is not None:
            body["response_format"] = (
                {"type": "json_object"} if self._json_mode_only else json_schema_format(json_schema)
            )
        return body

    def complete(
        self,
        messages: list[ChatMessage],
        max_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        check_request(messages, max_tokens)
        started = time.perf_counter()
        with self._lock:
            try:
                result = self._client.request(
                    "/chat/completions", self._body(messages, max_tokens, json_schema),
                    _TIMEOUT_SECONDS,
                )  # fmt: skip
            except ProviderRequestError as error:
                if error.status != 400 or json_schema is None or self._json_mode_only:
                    raise
                self._json_mode_only = True
                result = self._client.request(
                    "/chat/completions", self._body(messages, max_tokens, json_schema),
                    _TIMEOUT_SECONDS,
                )  # fmt: skip
        return parse_chat_completion(result, self.model_id, started)
