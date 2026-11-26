"""Embeddings from a remote provider's OpenAI-compatible API (`/embeddings`), over HTTPS.

Used only when the person chose a provider as the search model in Settings, accepting that the
indexed text of every project is sent to it. Same client and rules as the remote chat
(`RemoteHttpsClient`): HTTPS with certificate checks, no redirects, the key read per request.
"""

from collections.abc import Callable
from typing import Final

from scrinium.infrastructure.embeddings.base import QUERY_INSTRUCTION
from scrinium.infrastructure.local_http import ModelError, ModelStatus
from scrinium.infrastructure.remote_http import RemoteHttpsClient

_TIMEOUT_SECONDS: Final = 120
_STATUS_TIMEOUT_SECONDS: Final = 15


class RemoteOpenAIEmbeddings:
    def __init__(
        self,
        provider: str,
        base_url: str,
        model: str,
        api_key: Callable[[], str],
        client: RemoteHttpsClient | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._client = client or RemoteHttpsClient(
            base_url, lambda: {"Authorization": f"Bearer {api_key()}"}
        )

    @property
    def model_id(self) -> str:
        # The host, not the provider's display name: renaming it keeps the vectors.
        return f"remote:{self._client.host}/{self._model}"

    def status(self) -> ModelStatus:
        """One cheap request: the model list. A locked keyring or a bad key is not ready."""
        try:
            listing = self._client.request("/models", None, _STATUS_TIMEOUT_SECONDS)
        except Exception:
            return ModelStatus.UNAVAILABLE
        names = {
            str(entry.get("id"))
            for entry in (listing.get("data") if isinstance(listing, dict) else None) or []
            if isinstance(entry, dict)
        }
        # Some providers list no models; then the first embedding request decides.
        return ModelStatus.READY if not names or self._model in names else ModelStatus.MODEL_MISSING

    def _embed(self, texts: list[str]) -> list[list[float]]:
        body = {"model": self._model, "input": texts, "encoding_format": "float"}
        result = self._client.request("/embeddings", body, _TIMEOUT_SECONDS)
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise ModelError
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
