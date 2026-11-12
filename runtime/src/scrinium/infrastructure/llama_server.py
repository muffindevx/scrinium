"""llama-server managed by Scrinium: one process serves chat and embeddings (ADR-004).

It listens only on 127.0.0.1, on a free port, and requires a new key on every start: another
program on this machine cannot use it. The key goes through the environment, not the command
line, which any user of the machine can see. No network (`--offline`): the models are already
downloaded and verified. Each model loads with the first request that uses it.
"""

import contextlib
import ctypes
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from scrinium.core.config import LOOPBACK_HOST
from scrinium.infrastructure.llm.base import CONTEXT_TOKENS
from scrinium.infrastructure.tools.manifest import Model

LLAMA_DIRNAME: Final = "llama"
# An indexed chunk (6000 characters) fits easily; the whole batch is processed at once.
EMBEDDING_CONTEXT_TOKENS: Final = 8192
# llama-server's in-RAM prompt cache. By default it keeps up to 8 GiB and grows with each
# different prompt: in the pilot it took the chat process from 1.3 to 3.4 GB in 14 batches.
# 512 MiB is enough to reuse the shared start of the prompts; embeddings do not use it.
CHAT_CACHE_RAM_MIB: Final = 512
# V9: input tokens the GPU processes at once when reading. Reading is ~40 % of an extraction
# batch; llama-server's default is 512. Pinned with the V9 probe.
CHAT_UBATCH: Final = 512
# S1: chat model slots, that is, batches it can generate at once. One by default.
CHAT_SLOTS = 1
_READY_TIMEOUT_SECONDS: Final = 15
_STOP_TIMEOUT_SECONDS: Final = 10
_PR_SET_PDEATHSIG: Final = 1
# llama-server priority on Linux and macOS (0 normal, 19 lowest).
MODEL_NICENESS: Final = 10

# Its output goes to logging: the terminal with --verbose and `scrinium attach` see it.
llama_logger = logging.getLogger("scrinium.llama")


@dataclass(frozen=True)
class ServedModel:
    model: Model
    path: Path
    embeddings: bool = False
    # Models with optional reasoning (hybrid Qwen3): without reasoning, it answers directly.
    no_thinking: bool = False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK_HOST, 0))
        return int(probe.getsockname()[1])


def model_threads() -> int:
    """Half the cores: if the model falls back to CPU, the machine stays responsive."""
    return max(1, (os.cpu_count() or 2) // 2)


def presets(served: list[ServedModel]) -> str:
    """The router INI: one section per model, named after it."""
    sections = []
    for entry in served:
        slots = 1 if entry.embeddings else CHAT_SLOTS
        lines = [f"[{entry.model.name}]", f"model = {entry.path}", f"parallel = {slots}"]
        lines.append(f"threads = {model_threads()}")
        if entry.embeddings:
            size = EMBEDDING_CONTEXT_TOKENS
            lines += ["embeddings = true", f"ctx-size = {size}", "cache-ram = 0"]
            lines += [f"batch-size = {size}", f"ubatch-size = {size}"]
        else:
            # Pinned: without it, llama-server would fit it to free memory and could end up
            # below what Scrinium sends.
            # The context is split among the slots: each one gets the full context.
            lines.append(f"ctx-size = {CONTEXT_TOKENS * slots}")
            # 8-bit context cache: half the memory (~0.9 GB less with 12288 tokens),
            # in VRAM, or in RAM if the GPU has no room. Embeddings stay as they are: their model is
            # untouched, so already indexed vectors remain comparable.
            lines += ["cache-type-k = q8_0", "cache-type-v = q8_0"]
            lines.append(f"cache-ram = {CHAT_CACHE_RAM_MIB}")
            # N-gram speculative decoding was removed: with short answers it only accepted
            # 4-8 % of what it proposed.
            lines.append(f"ubatch-size = {CHAT_UBATCH}")
            if entry.no_thinking:
                lines.append("reasoning-budget = 0")
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + "\n"


def _die_with_parent() -> None:
    """Linux: if Scrinium dies without stopping it, the kernel stops llama-server."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)


def _child_setup() -> None:
    """Low priority: with a busy CPU, the person's applications go first."""
    with contextlib.suppress(OSError):
        os.nice(MODEL_NICENESS)
    if sys.platform.startswith("linux"):
        _die_with_parent()


def _priority_options() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.BELOW_NORMAL_PRIORITY_CLASS}
    return {"preexec_fn": _child_setup}


class LlamaServer:
    def __init__(self, executable: Path, served: list[ServedModel], workdir: Path) -> None:
        self._executable = executable
        self._served = served
        self._workdir = workdir
        self._process: subprocess.Popen[bytes] | None = None
        self.api_key = secrets.token_urlsafe(32)
        self.url: str | None = None

    def start(self) -> bool:
        """Starts it and waits for an answer. False on failure: Scrinium goes on without models."""
        self._workdir.mkdir(mode=0o700, exist_ok=True)
        preset_file = self._workdir / "presets.ini"
        preset_file.write_text(presets(self._served), encoding="utf-8")
        preset_file.chmod(0o600)
        port = _free_port()
        command = [
            str(self._executable),
            "--host", LOOPBACK_HOST,
            "--port", str(port),
            "--models-preset", str(preset_file),
            "--models-max", str(len(self._served)),
            "--no-webui",
            "--offline",
        ]  # fmt: skip
        try:
            self._process = subprocess.Popen(  # noqa: S603  verified executable, no shell
                command,
                cwd=self._workdir,
                env={**os.environ, "LLAMA_API_KEY": self.api_key},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                **_priority_options(),
            )
        except OSError:
            return False
        threading.Thread(target=_forward_output, args=(self._process,), daemon=True).start()
        url = f"http://{LOOPBACK_HOST}:{port}"
        deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                return False
            if _healthy(url):
                self.url = url
                return True
            time.sleep(0.2)
        self.stop()
        return False

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def stop(self) -> None:
        process, self._process = self._process, None
        self.url = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _healthy(url: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with (
        contextlib.suppress(urllib.error.URLError, OSError),
        opener.open(f"{url}/health", timeout=1) as response,
    ):
        return bool(response.status == 200)
    return False


def _forward_output(process: "subprocess.Popen[bytes]") -> None:
    """Always reads the output, even unwatched: if the pipe fills up, llama-server stalls."""
    if process.stdout is None:
        return
    for raw in process.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        if line:
            llama_logger.info("%s", line)
