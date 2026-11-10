"""Tools and models Scrinium downloads: pinned version and SHA256 of each file.

The SHA256 values are those GitHub publishes for each release asset, and Hugging Face for each
GGUF. To update something, change its version (or commit), URL, size and SHA256 together here.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class Variant(StrEnum):
    DEFAULT = "default"
    # llama.cpp on Linux and Windows: GPU through Vulkan, or CPU only if the system has no Vulkan.
    VULKAN = "vulkan"
    CPU = "cpu"


@dataclass(frozen=True)
class Asset:
    url: str
    sha256: str
    size: int
    # Path of the executable inside the unpacked archive.
    executable: str


@dataclass(frozen=True)
class Tool:
    name: str
    version: str
    # (sistema, arquitectura, variante)
    assets: Mapping[tuple[str, str, Variant], Asset]


_GITLEAKS_RELEASE = "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/"
_LLAMA_RELEASE = "https://github.com/ggml-org/llama.cpp/releases/download/b12110/"

GITLEAKS = Tool(
    "gitleaks",
    "8.30.1",
    {
        ("linux", "x64", Variant.DEFAULT): Asset(
            _GITLEAKS_RELEASE + "gitleaks_8.30.1_linux_x64.tar.gz",
            "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
            8230402,
            "gitleaks",
        ),
        ("linux", "arm64", Variant.DEFAULT): Asset(
            _GITLEAKS_RELEASE + "gitleaks_8.30.1_linux_arm64.tar.gz",
            "e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080",
            7601421,
            "gitleaks",
        ),
        ("macos", "x64", Variant.DEFAULT): Asset(
            _GITLEAKS_RELEASE + "gitleaks_8.30.1_darwin_x64.tar.gz",
            "dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709",
            8359235,
            "gitleaks",
        ),
        ("macos", "arm64", Variant.DEFAULT): Asset(
            _GITLEAKS_RELEASE + "gitleaks_8.30.1_darwin_arm64.tar.gz",
            "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5",
            7897593,
            "gitleaks",
        ),
        ("windows", "x64", Variant.DEFAULT): Asset(
            _GITLEAKS_RELEASE + "gitleaks_8.30.1_windows_x64.zip",
            "d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e",
            8438883,
            "gitleaks.exe",
        ),
        ("windows", "arm64", Variant.DEFAULT): Asset(
            _GITLEAKS_RELEASE + "gitleaks_8.30.1_windows_arm64.zip",
            "b95f5e4f5c425cedca7ee203d9afd29597e692c4924a12ed42f970537c72cc0f",
            7701820,
            "gitleaks.exe",
        ),
    },
)

# llama.cpp v0.9.0 publishes its binaries in build b12110.
LLAMA_SERVER = Tool(
    "llama-server",
    "b12110",
    {
        ("linux", "x64", Variant.CPU): Asset(
            _LLAMA_RELEASE + "llama-b12110-bin-ubuntu-x64.tar.gz",
            "368227edcc229be5f9bed16a2223d79b5e41d0ac2e19bcf5825e82291c060e55",
            23051958,
            "llama-b12110/llama-server",
        ),
        ("linux", "x64", Variant.VULKAN): Asset(
            _LLAMA_RELEASE + "llama-b12110-bin-ubuntu-vulkan-x64.tar.gz",
            "a5d741940956ec62e8783aee7e5a4c7c761af867d6f027c3277a51e1a3ac59b0",
            35858360,
            "llama-b12110/llama-server",
        ),
        ("linux", "arm64", Variant.CPU): Asset(
            _LLAMA_RELEASE + "llama-b12110-bin-ubuntu-arm64.tar.gz",
            "4758b51335e37b4346251f2687d64841ff1c07ecb27fc46323945bb2ec9c44d2",
            18723578,
            "llama-b12110/llama-server",
        ),
        ("linux", "arm64", Variant.VULKAN): Asset(
            _LLAMA_RELEASE + "llama-b12110-bin-ubuntu-vulkan-arm64.tar.gz",
            "1c68bf23d35230cc607e3d30f8d447d21fef500d9fa8d65e15520dfa3619ad73",
            29168359,
            "llama-b12110/llama-server",
        ),
        ("macos", "x64", Variant.DEFAULT): Asset(
            _LLAMA_RELEASE + "llama-b12110-bin-macos-x64.tar.gz",
            "c8094268b6bf62f4998f0e45656fd9f5e2d4afb6b1cd0044d761db64be1dd47d",
            12388601,
            "llama-b12110/llama-server",
        ),
        ("macos", "arm64", Variant.DEFAULT): Asset(
            _LLAMA_RELEASE + "llama-b12110-bin-macos-arm64.tar.gz",
            "4f5b8de12e0b9516658ca6a5764c6ffb3d7c659c3dd538556b1413c7b4050662",
            16650691,
            "llama-b12110/llama-server",
        ),
        ("windows", "x64", Variant.CPU): Asset(
            _LLAMA_RELEASE + "llama-b12110-bin-win-cpu-x64.zip",
            "98901e327c252875accaf2d1bf364e10e868843b0b63468baad1aa945cdc5da2",
            24708445,
            "llama-server.exe",
        ),
        ("windows", "x64", Variant.VULKAN): Asset(
            _LLAMA_RELEASE + "llama-b12110-bin-win-vulkan-x64.zip",
            "9732a5e48887ca54d0bfe4af4324dc85fd56cd8cda19b52df3542fa6ebe6874f",
            37661146,
            "llama-server.exe",
        ),
        ("windows", "arm64", Variant.CPU): Asset(
            _LLAMA_RELEASE + "llama-b12110-bin-win-cpu-arm64.zip",
            "f8d20fb07d0b499988ced8963afcd5ded2d2cdd9983b757fbc7e51c4abd722e8",
            13197637,
            "llama-server.exe",
        ),
        ("windows", "arm64", Variant.VULKAN): Asset(
            _LLAMA_RELEASE + "llama-b12110-bin-win-vulkan-arm64.zip",
            "2be672cf538aabfc9fcb22c21648422b01d623673f15c2742bd43a9bd1a11ce2",
            25728494,
            "llama-server.exe",
        ),
    },
)

TOOLS: Mapping[str, Tool] = {tool.name: tool for tool in (GITLEAKS, LLAMA_SERVER)}


@dataclass(frozen=True)
class Model:
    """A GGUF from Hugging Face, pinned to a repository commit: the file never changes."""

    # The name Scrinium uses to ask llama-server for it.
    name: str
    repo: str
    revision: str
    file: str
    sha256: str
    size: int

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{self.file}"


# The same model as Ollama's `qwen3:4b-instruct`. Apache-2.0.
CHAT_MODEL = Model(
    "qwen3-4b-instruct-2507",
    "unsloth/Qwen3-4B-Instruct-2507-GGUF",
    "a06e946bb6b655725eafa393f4a9745d460374c9",
    "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
    "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597",
    2497281120,
)
# Qwen's official one, like Ollama's `qwen3-embedding:0.6b`. Apache-2.0.
EMBEDDING_MODEL = Model(
    "qwen3-embedding-0.6b",
    "Qwen/Qwen3-Embedding-0.6B-GGUF",
    "370f27d7550e0def9b39c1f16d3fbaa13aa67728",
    "Qwen3-Embedding-0.6B-Q8_0.gguf",
    "06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439",
    639150592,
)

# Evaluated for the rule map (extraction phase 1) and rejected on 2026-11-10: 2.6x
# faster, but it found 9 of 28 reference rules versus 24 with the 4B (PERFORMANCE.md).
# Not in MODELS: not installed; the benchmark uses it with --map-model if present. Apache-2.0.
MAP_MODEL = Model(
    "qwen3-1.7b",
    "unsloth/Qwen3-1.7B-GGUF",
    "d7f544eead698dbd1f15126ef60b45a1e1933222",
    "Qwen3-1.7B-Q4_K_M.gguf",
    "b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897",
    1107409472,
)

MODELS: Mapping[str, Model] = {model.name: model for model in (CHAT_MODEL, EMBEDDING_MODEL)}
