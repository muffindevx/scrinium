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
_LLAMA_RELEASE = "https://github.com/ggml-org/llama.cpp/releases/download/b12359/"

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

# llama.cpp v1.0.0 publishes its binaries in build b12359.
LLAMA_SERVER = Tool(
    "llama-server",
    "b12359",
    {
        ("linux", "x64", Variant.CPU): Asset(
            _LLAMA_RELEASE + "llama-b12359-bin-ubuntu-x64.tar.gz",
            "0bc1f091e8916102b6d8f2cf944233a620e90a0f856c72d0ce41150c65c2e72e",
            23311084,
            "llama-b12359/llama-server",
        ),
        ("linux", "x64", Variant.VULKAN): Asset(
            _LLAMA_RELEASE + "llama-b12359-bin-ubuntu-vulkan-x64.tar.gz",
            "19b60f00395a3f4954e46a5037695bc1d843404fc9d3124f82a612ecd8fbe9f0",
            36153377,
            "llama-b12359/llama-server",
        ),
        ("linux", "arm64", Variant.CPU): Asset(
            _LLAMA_RELEASE + "llama-b12359-bin-ubuntu-arm64.tar.gz",
            "dc7aa17a67884619b7223870aca9fd6addbc01717713b04a714add1c31137359",
            18949862,
            "llama-b12359/llama-server",
        ),
        ("linux", "arm64", Variant.VULKAN): Asset(
            _LLAMA_RELEASE + "llama-b12359-bin-ubuntu-vulkan-arm64.tar.gz",
            "3f8ae812a58ac92e5a596ca9fbc988f52854d147c5a7fe1264dddc90c1d085b4",
            29435221,
            "llama-b12359/llama-server",
        ),
        ("macos", "x64", Variant.DEFAULT): Asset(
            _LLAMA_RELEASE + "llama-b12359-bin-macos-x64.tar.gz",
            "25320d529077ef5a3d16e7b5da26438f221d88c8c0930a32b0c79457f5eede56",
            12661621,
            "llama-b12359/llama-server",
        ),
        ("macos", "arm64", Variant.DEFAULT): Asset(
            _LLAMA_RELEASE + "llama-b12359-bin-macos-arm64.tar.gz",
            "82b00abc740160e1b9980e6cc79c117345fa34af058c80e6c0a69aa8ddccde75",
            16907811,
            "llama-b12359/llama-server",
        ),
        ("windows", "x64", Variant.CPU): Asset(
            _LLAMA_RELEASE + "llama-b12359-bin-win-cpu-x64.zip",
            "837c1655e87df3b66989f4be33a792a1399c785bf259038dc75c900096683e69",
            25097509,
            "llama-server.exe",
        ),
        ("windows", "x64", Variant.VULKAN): Asset(
            _LLAMA_RELEASE + "llama-b12359-bin-win-vulkan-x64.zip",
            "e8d18c6e4210cca87e7307c2ea78363ac8d158e4e1397358ec96ab70b53a0d5e",
            38090643,
            "llama-server.exe",
        ),
        ("windows", "arm64", Variant.CPU): Asset(
            _LLAMA_RELEASE + "llama-b12359-bin-win-cpu-arm64.zip",
            "d27564c754478d2832789dfcebfc5ace018e36a224c41d810b7cbc1ba8243ec0",
            13455326,
            "llama-server.exe",
        ),
        ("windows", "arm64", Variant.VULKAN): Asset(
            _LLAMA_RELEASE + "llama-b12359-bin-win-vulkan-arm64.zip",
            "62cd50089cffdd825524a91c5a8819041eb75d8e2beacd6eca68194dba50574f",
            26027698,
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
# faster, but it found 9 of 28 reference rules versus 24 with the 4B.
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
