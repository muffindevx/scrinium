"""Claude through Anthropic's official Python SDK (ADR-010).

- The key is passed explicitly, read from the keyring for each request: the SDK then reads no
  credentials from the environment or from `ant` profiles. The client lives for one request.
- Only text blocks are read: thinking blocks are dropped, never stored or logged.
- Structured output with `output_config.format`. If the API rejects the schema (HTTP 400),
  the adapter asks again without it, on the same model, and keeps doing so; the task still
  validates every answer against its schema.
- No server-side fallback to another model: a refusal is a refused batch (ADR-010).
- No sampling parameters: current Claude models reject them.
- The SDK retries what is transient (rate limits, overload, connection) on the same request.
"""

import threading
import time
from collections.abc import Callable
from typing import Any, Final

import anthropic

from scrinium.infrastructure.llm.base import (
    ChatMessage,
    Completion,
    ModelRefusedError,
    OutputBudgetExceededError,
    check_request,
)
from scrinium.infrastructure.local_http import ModelError, ModelStatus, ProviderUnavailableError
from scrinium.infrastructure.remote_http import (
    ProviderAuthError,
    ProviderRequestError,
    check_https_url,
)

_TIMEOUT_SECONDS: Final = 300
_RETRIES: Final = 2
_STATUS_PROMPT: Final = [ChatMessage("user", "Responde solo con la palabra: listo")]

ClientFactory = Callable[..., Any]


def _client(**options: Any) -> Any:
    return anthropic.Anthropic(**options)


def _usage(usage: Any) -> tuple[int, int, int]:
    """(all input, cached input, output). Anthropic's `input_tokens` leaves out cached tokens,
    so they are added: the same total every other provider reports."""
    read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    written = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    return int(usage.input_tokens or 0) + read + written, read, int(usage.output_tokens or 0)


class AnthropicChat:
    def __init__(
        self,
        provider: str,
        base_url: str,
        model: str,
        api_key: Callable[[], str],
        effort: str | None = None,
        client_factory: ClientFactory = _client,
    ) -> None:
        self._provider = provider
        self._base_url = check_https_url(base_url)
        self._model = model
        self._api_key = api_key
        self._effort = effort
        self._factory = client_factory
        self._without_schema = False
        # One request at a time in the first version (ADR-010).
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return f"{self._provider}:{self._model}"

    @property
    def is_local(self) -> bool:
        return False

    def status(self) -> ModelStatus:
        """No request: it would spend tokens. See `check()`."""
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

    def _send(self, request: dict[str, Any]) -> Any:
        client = self._factory(
            api_key=self._api_key(), base_url=self._base_url, max_retries=_RETRIES,
            timeout=_TIMEOUT_SECONDS,
        )  # fmt: skip
        try:
            return client.messages.create(**request)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise ProviderAuthError from None
        except (anthropic.RateLimitError, anthropic.InternalServerError):
            raise ProviderUnavailableError from None
        except anthropic.APIStatusError as error:
            if error.status_code >= 500:
                raise ProviderUnavailableError from None
            raise ProviderRequestError(error.status_code) from None
        except (anthropic.APIConnectionError, anthropic.APITimeoutError):
            raise ProviderUnavailableError from None
        except anthropic.AnthropicError:
            raise ModelError from None
        finally:
            client.close()

    def _request(
        self, messages: list[ChatMessage], max_tokens: int, json_schema: dict[str, Any] | None
    ) -> dict[str, Any]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": m.role, "content": m.content} for m in messages if m.role != "system"
            ],
        }
        if system:
            request["system"] = system
        output: dict[str, Any] = {}
        if json_schema is not None and not self._without_schema:
            output["format"] = {"type": "json_schema", "schema": json_schema}
        if self._effort is not None:
            output["effort"] = self._effort
        if output:
            request["output_config"] = output
        return request

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
                response = self._send(self._request(messages, max_tokens, json_schema))
            except ProviderRequestError as error:
                if error.status != 400 or json_schema is None or self._without_schema:
                    raise
                self._without_schema = True
                response = self._send(self._request(messages, max_tokens, json_schema))
        if response.stop_reason == "refusal":
            raise ModelRefusedError
        if response.stop_reason == "max_tokens":
            raise OutputBudgetExceededError
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()
        if not text:
            raise ModelError
        prompt, cached, output = _usage(response.usage)
        return Completion(
            text=text,
            # The model that actually answered.
            model_id=f"{self._provider}:{response.model}",
            prompt_tokens=prompt,
            completion_tokens=output,
            cached_tokens=cached,
            duration_ms=int((time.perf_counter() - started) * 1000),
            request_id=str(response.id)[:200],
        )
