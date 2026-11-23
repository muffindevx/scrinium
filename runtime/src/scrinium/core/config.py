"""Configuration of the local runtime."""

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final

from platformdirs import user_data_dir

# A constant on purpose: there is no option to listen on another network interface.
LOOPBACK_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 47615
DEFAULT_WEB_DIST: Final = Path(__file__).resolve().parent.parent / "web_dist"


def default_data_dir() -> Path:
    override = os.environ.get("SCRINIUM_DATA_DIR")
    return Path(override) if override else Path(user_data_dir("scrinium", appauthor=False))


def default_port() -> int:
    return int(os.environ.get("SCRINIUM_PORT", DEFAULT_PORT))


class ModelProvider(StrEnum):
    # llama-server, which Scrinium starts and stops; the models, from `scrinium tools install`.
    LLAMA = "llama"
    # An Ollama already running on this machine, with its own models.
    OLLAMA = "ollama"


def default_model_provider() -> ModelProvider:
    """Raises ValueError if SCRINIUM_MODEL_PROVIDER is neither `llama` nor `ollama`."""
    return ModelProvider(os.environ.get("SCRINIUM_MODEL_PROVIDER", ModelProvider.LLAMA))


def default_ollama_url() -> str:
    # Scrinium's own Ollama (`make ollama`), not the system one.
    return os.environ.get("SCRINIUM_OLLAMA_URL", "http://127.0.0.1:11435")


def default_llm_model() -> str:
    return os.environ.get("SCRINIUM_LLM_MODEL", "qwen3:4b-instruct")


def default_local_chat_model() -> str | None:
    """Which of llama-server's chat models writes (the manifest's names); None, the default."""
    return os.environ.get("SCRINIUM_LOCAL_CHAT_MODEL") or None


def default_embedding_model() -> str:
    return os.environ.get("SCRINIUM_EMBEDDING_MODEL", "qwen3-embedding:0.6b")


@dataclass(frozen=True)
class RuntimeConfig:
    port: int
    data_dir: Path
    web_dist: Path = DEFAULT_WEB_DIST
    model_provider: ModelProvider = ModelProvider.LLAMA
    # Loopback only: validated when building the provider (ADR-004).
    # Without a URL, llama-server is not running and the models show as unavailable.
    llama_url: str | None = None
    llama_api_key: str | None = field(default=None, repr=False)
    # Key for `scrinium stop` and `scrinium attach`; without it, the /control/ routes do not exist.
    control_token: str | None = field(default=None, repr=False)
    ollama_url: str = "http://127.0.0.1:11435"
    # Ollama's models; llama-server's are the ones in the manifest.
    embedding_model: str = "qwen3-embedding:0.6b"
    llm_model: str = "qwen3:4b-instruct"
    # llama-server's chat model (SCRINIUM_LOCAL_CHAT_MODEL); None is the managed default.
    local_chat_model: str | None = None

    @property
    def allowed_host(self) -> str:
        return f"{LOOPBACK_HOST}:{self.port}"

    @property
    def allowed_origin(self) -> str:
        return f"http://{self.allowed_host}"
