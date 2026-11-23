"""What Settings shows about the local AI engine. Read only: it changes nothing on the machine."""

from typing import Any

from scrinium.core.config import ModelProvider, RuntimeConfig
from scrinium.core.memory import total_memory_bytes
from scrinium.infrastructure.tools import downloads
from scrinium.infrastructure.tools.installer import (
    ToolError,
    current_platform,
    locate,
    locate_model,
)
from scrinium.infrastructure.tools.manifest import EMBEDDING_MODEL, LLAMA_SERVER, local_chat_model


def _accelerator(os_name: str, executable: str | None) -> str | None:
    if executable is None:
        return None
    if os_name == "macos":
        return "metal"
    return "vulkan" if "-vulkan" in executable else "cpu"


def engine_info(config: RuntimeConfig) -> dict[str, Any]:
    try:
        os_name, arch = current_platform()
    except ToolError:
        os_name, arch = "", ""
    executable = (
        locate(LLAMA_SERVER, downloads.tools_dir(config.data_dir), os_name, arch)
        if os_name
        else None
    )
    models_dir = downloads.models_dir(config.data_dir)
    return {
        "provider": config.model_provider.value,
        "memory_bytes": total_memory_bytes(),
        "engine": {
            "name": "llama.cpp",
            "version": LLAMA_SERVER.version,
            "installed": executable is not None,
            "running": config.model_provider == ModelProvider.LLAMA
            and config.llama_url is not None,
            "accelerator": _accelerator(os_name, str(executable) if executable else None),
        },
        "models": [
            {
                "role": role,
                "name": model.name,
                "file": model.file,
                "size_bytes": model.size,
                "installed": locate_model(model, models_dir) is not None,
            }
            for role, model in (
                ("chat", local_chat_model(config.local_chat_model)),
                ("embeddings", EMBEDDING_MODEL),
            )
        ],
    }
