"""llama-server in router mode: the chat model loads before the embedding one (V4).

llama-server splits the GPU when loading each model (`--fit`) and leaves a free margin. If the
embedding model already holds its share, the chat model does not fit whole: some layers go to
the CPU and it generates at ~12 tokens/s instead of ~43 (GTX 1060 6 GB, measured 2026-11-10).
Unloading the embeddings afterwards does not fix it: layers are split at load time. Conversely,
if chat is already loaded, the embeddings fit in what is left and chat stays at ~43 tokens/s.

So, before loading chat, the embeddings are unloaded, and they reload by themselves with
the next request that uses them. If something fails here nothing is cut off: the request loads
the model on its own, as before, only perhaps slower.
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Final

from scrinium.infrastructure.local_http import LocalHttpClient, ModelError

logger = logging.getLogger("scrinium.models")

LOADED: Final = "loaded"
UNLOADED: Final = "unloaded"
_REQUEST_TIMEOUT_SECONDS: Final = 10
# Chat takes ~2.5 s to load from the system cache; longer from a slow disk.
_WAIT_SECONDS: Final = 120
_POLL_SECONDS: Final = 0.1


class ChatFirst:
    def __init__(
        self,
        client: LocalHttpClient,
        chat_model: str,
        embedding_model: str,
        wait_seconds: float = _WAIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._chat = chat_model
        self._embedding = embedding_model
        self._wait_seconds = wait_seconds
        self._clock = clock
        self._sleep = sleep
        # While chat is being prepared, an embedding request waits: if it slipped in,
        # it would load the embeddings first and chat would again end up half on the GPU.
        self._lock = threading.Lock()

    def before_chat(self) -> None:
        with self._lock:
            try:
                self._prepare_chat()
            except ModelError:
                logger.warning("chat_first_failed")

    def before_embeddings(self) -> None:
        with self._lock:
            pass

    def _prepare_chat(self) -> None:
        states = self._states()
        if states.get(self._chat, UNLOADED) != UNLOADED:
            return  # loaded or loading: the order is already decided
        if states.get(self._embedding, UNLOADED) != UNLOADED:
            logger.info("chat_first unloading=%s", self._embedding)
            self._client.request(
                "/models/unload", {"model": self._embedding}, _REQUEST_TIMEOUT_SECONDS
            )
            # Unloading is asynchronous: the memory is not free until the status says so.
            if not self._wait(self._embedding, UNLOADED):
                return
        self._client.request("/models/load", {"model": self._chat}, _REQUEST_TIMEOUT_SECONDS)
        self._wait(self._chat, LOADED)

    def _wait(self, model: str, state: str) -> bool:
        deadline = self._clock() + self._wait_seconds
        while self._clock() < deadline:
            if self._states().get(model) == state:
                return True
            self._sleep(_POLL_SECONDS)
        logger.warning("chat_first_timeout model=%s state=%s", model, state)
        return False

    def _states(self) -> dict[str, str]:
        listing: Any = self._client.request("/models", None, _REQUEST_TIMEOUT_SECONDS)
        entries = listing.get("data") if isinstance(listing, dict) else None
        states: dict[str, str] = {}
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                continue
            status = entry.get("status")
            value = status.get("value") if isinstance(status, dict) else status
            if isinstance(value, str):
                states[entry["id"]] = value
        return states
