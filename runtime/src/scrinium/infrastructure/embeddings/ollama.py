"""Embeddings with Ollama on this same machine (ADR-004), the alternative to llama.cpp."""

from typing import Final

from scrinium.infrastructure.embeddings.base import QUERY_INSTRUCTION
from scrinium.infrastructure.local_http import ModelError, ModelStatus
from scrinium.infrastructure.ollama_client import OllamaClient

_TIMEOUT_SECONDS: Final = 300


class OllamaEmbeddings:
    def __init__(self, base_url: str, model: str) -> None:
        self._client = OllamaClient(base_url)
        self._model = model

    @property
    def model_id(self) -> str:
        return f"ollama:{self._model}"

    def status(self) -> ModelStatus:
        return self._client.model_status(self._model)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        body = {"model": self._model, "input": texts, "truncate": True}
        result = self._client.request("/api/embed", body, _TIMEOUT_SECONDS)
        vectors = result.get("embeddings") if isinstance(result, dict) else None
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ModelError
        return [[float(value) for value in vector] for vector in vectors]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([QUERY_INSTRUCTION + text])[0]
