"""Ollama client on this same machine (ADR-004): Ollama's own API, not OpenAI's.

The own API is used because it allows pinning the context on each request (`num_ctx`); with the
OpenAI-compatible one, Ollama would silently trim a prompt longer than its context.
"""

from typing import Final

from scrinium.infrastructure.local_http import LocalHttpClient, ModelError, ModelStatus

_STATUS_TIMEOUT_SECONDS: Final = 3


class OllamaClient(LocalHttpClient):
    def model_status(self, model: str) -> ModelStatus:
        try:
            tags = self.request("/api/tags", None, _STATUS_TIMEOUT_SECONDS)
        except ModelError:
            return ModelStatus.UNAVAILABLE
        names = {entry.get("name") for entry in tags.get("models", [])}
        if model in names or f"{model}:latest" in names:
            return ModelStatus.READY
        return ModelStatus.MODEL_MISSING
