"""Embeddings through the OpenAI-compatible API: llama-server's, managed by Scrinium."""

from collections.abc import Callable
from typing import Final

from scrinium.infrastructure.embeddings.base import QUERY_INSTRUCTION
from scrinium.infrastructure.llm.openai_compatible import openai_model_status
from scrinium.infrastructure.local_http import LocalHttpClient, ModelError, ModelStatus

_TIMEOUT_SECONDS: Final = 300


class OpenAICompatibleEmbeddings:
    def __init__(
        self,
        base_url: str | None,
        model: str,
        api_key: str | None = None,
        provider: str = "llama",
        before_request: Callable[[], None] | None = None,
    ) -> None:
        """`before_request` is called before each request: with llama-server, `ChatFirst`."""
        self._client = LocalHttpClient(base_url, api_key) if base_url is not None else None
        self._model = model
        self._provider = provider
        self._before_request = before_request

    @property
    def model_id(self) -> str:
        return f"{self._provider}:{self._model}"

    def status(self) -> ModelStatus:
        return openai_model_status(self._client, self._model)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            raise ModelError
        if self._before_request is not None:
            self._before_request()
        body = {"model": self._model, "input": texts, "encoding_format": "float"}
        result = self._client.request("/v1/embeddings", body, _TIMEOUT_SECONDS)
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise ModelError
        # Order comes from `index`, not from the position in the list.
        try:
            ordered = sorted(data, key=lambda entry: int(entry["index"]))
            vectors = [[float(value) for value in entry["embedding"]] for entry in ordered]
        except (KeyError, TypeError, ValueError):
            raise ModelError from None
        if [int(entry["index"]) for entry in ordered] != list(range(len(texts))):
            raise ModelError
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([QUERY_INSTRUCTION + text])[0]
