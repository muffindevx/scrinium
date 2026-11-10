"""What local embeddings share: the provider contract and the query instruction."""

from typing import Final, Protocol

from scrinium.infrastructure.local_http import ModelStatus

# Qwen3-Embedding recommends an instruction on queries, not on documents.
QUERY_INSTRUCTION: Final = (
    "Instruct: Given a question about a software system, retrieve the code or documentation "
    "passages that answer it\nQuery: "
)


class EmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str:
        """Stable identifier: if it changes, earlier vectors are no longer usable."""

    def status(self) -> ModelStatus: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...
