"""`scrinium doctor`: checks the machine and says how to fix what is missing.

It only reads: it downloads, installs and changes nothing, except creating the private data
directory if it does not exist yet (as at startup). Exits with 1 if something blocks Scrinium.
"""

import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from scrinium import __version__
from scrinium.cli.window import window_available
from scrinium.core.config import ModelProvider, default_model_provider, default_ollama_url
from scrinium.core.data_dir import DataDirPermissions, UnsafeDataDirError, prepare_data_dir
from scrinium.core.disk_encryption import DiskEncryption, check_disk_encryption
from scrinium.core.memory import total_memory_bytes
from scrinium.infrastructure.local_http import LocalHttpClient, ModelError
from scrinium.infrastructure.secret_store import KeyringSecretStore
from scrinium.infrastructure.tools import downloads
from scrinium.infrastructure.tools.installer import (
    ToolError,
    current_platform,
    has_vulkan,
    locate,
    locate_model,
)
from scrinium.infrastructure.tools.manifest import (
    CHAT_MODEL,
    DEFAULT_INSTALL,
    LLAMA_SERVER,
    LOCAL_ADDON,
    MODELS,
    TOOLS,
)

_GB: Final = 1024**3
# Chat (2.5 GB) and embeddings (0.6 GB) loaded, with their context, plus Scrinium and the system.
_MIN_MEMORY_GB: Final = 8
# Without the local chat model (ADR-013): embeddings, Scrinium and the system.
_MIN_MEMORY_GB_NO_CHAT: Final = 4
_RUN_TIMEOUT_SECONDS: Final = 20
_INSTALL_FIX: Final = "Install it with: scrinium tools install"


class Level(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class Check:
    level: Level
    message: str
    fix: str | None = None


def _data_dir(data_dir: Path) -> Check:
    try:
        permissions = prepare_data_dir(data_dir)
    except UnsafeDataDirError as error:
        from scrinium.cli.main import unsafe_data_dir_message

        return Check(Level.FAIL, unsafe_data_dir_message(error.reason).format(path=data_dir))
    if permissions == DataDirPermissions.NOT_CHECKED:
        return Check(Level.WARN, f"Data directory, permissions not checked: {data_dir}")
    return Check(Level.OK, f"Private data directory: {data_dir}")


def _disk_encryption(data_dir: Path) -> Check:
    state = check_disk_encryption(data_dir)
    if state == DiskEncryption.ENCRYPTED:
        return Check(Level.OK, "Encrypted disk")
    return Check(
        Level.WARN,
        "No disk encryption detected: if you lose the machine, your data could be read.",
        "Turn on BitLocker, FileVault or LUKS, depending on your system.",
    )


def _keyring() -> Check:
    name = KeyringSecretStore.from_system().backend_name
    if name is not None:
        return Check(Level.OK, f"System secret store: {name}")
    fix = "On Linux, install GNOME Keyring or KWallet and log in to the desktop."
    return Check(Level.WARN, "No secret store: Scrinium will not be able to keep access keys.", fix)


def _memory(local_chat: bool) -> Check:
    total = total_memory_bytes()
    if total is None:
        return Check(Level.WARN, "Could not tell how much memory the machine has.")
    gigabytes = round(total / _GB)
    needed = _MIN_MEMORY_GB if local_chat else _MIN_MEMORY_GB_NO_CHAT
    if gigabytes < needed:
        return Check(
            Level.WARN,
            f"Memory: {gigabytes} GB. With less than {needed} GB, answers "
            "will be slow or the machine will be tight.",
            "Close other heavy programs (browsers, virtual machines) while you use Scrinium.",
        )
    return Check(Level.OK, f"Memory: {gigabytes} GB")


def _disk_space(data_dir: Path) -> Check:
    root = downloads.downloads_dir(data_dir)
    missing = sum(
        m.size
        for m in MODELS.values()
        if m.name in DEFAULT_INSTALL and locate_model(m, downloads.models_dir(data_dir)) is None
    )
    free = shutil.disk_usage(root if root.is_dir() else data_dir).free
    if free < missing + _GB:
        return Check(
            Level.FAIL,
            f"Free space for the downloads: {free // _GB} GB. The missing models "
            f"need {missing // _GB + 1} GB.",
            "Free some space or move the downloads to a bigger disk: scrinium tools move <folder>",
        )
    return Check(Level.OK, f"Downloads in {root}: {free // _GB} GB free")


def _verified(data_dir: Path, path: Path, label: str) -> Check:
    """Every byte against what was installed (ADR-015); only outside the data directory."""
    try:
        downloads.verify(data_dir, path, full=True)
    except ToolError as error:
        return Check(Level.FAIL, str(error), _INSTALL_FIX)
    return Check(Level.OK, label)


def _tools(data_dir: Path, os_name: str, arch: str) -> list[Check]:
    checks = []
    for tool in TOOLS.values():
        tools_dir = downloads.tools_dir(data_dir)
        path = locate(tool, tools_dir, os_name, arch)
        if path is None:
            checks.append(
                Check(Level.FAIL, f"{tool.name} {tool.version} is missing.", _INSTALL_FIX)
            )
        else:
            tree = tools_dir / Path(*path.relative_to(tools_dir).parts[:2])
            checks.append(_verified(data_dir, tree, f"{tool.name} {tool.version}"))
    for model in MODELS.values():
        model_file = locate_model(model, downloads.models_dir(data_dir))
        if model_file is None:
            if model.name not in DEFAULT_INSTALL:
                checks.append(
                    Check(
                        Level.OK,
                        f"Local chat model {model.name}: not installed (optional, "
                        f"scrinium tools install {LOCAL_ADDON})",
                    )
                )
                continue
            checks.append(Check(Level.FAIL, f"The model {model.name} is missing.", _INSTALL_FIX))
        else:
            checks.append(_verified(data_dir, model_file, f"Model {model.name}"))
    return checks


def _llama_runs(data_dir: Path, os_name: str, arch: str) -> Check | None:
    """Runs `llama-server --version`: fails if libraries are missing or the CPU is unsuitable."""
    tools_dir = downloads.tools_dir(data_dir)
    executable = locate(LLAMA_SERVER, tools_dir, os_name, arch)
    if executable is None:
        return None
    try:
        tree = tools_dir / Path(*executable.relative_to(tools_dir).parts[:2])
        downloads.verify(data_dir, tree, full=True)
    except ToolError:
        return None  # Already reported: it is not run.
    try:
        result = subprocess.run(  # noqa: S603  installed, verified executable
            [executable, "--version"],
            capture_output=True,
            timeout=_RUN_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode != 0:
        return Check(
            Level.FAIL,
            "llama-server cannot run on this machine.",
            "Try the CPU-only version: scrinium tools install --cpu",
        )
    gpu = "Metal" if os_name == "macos" else "Vulkan" if "vulkan" in str(executable) else "CPU"
    return Check(Level.OK, f"llama-server runs ({gpu})")


def _gpu(os_name: str) -> Check:
    if os_name == "macos" or has_vulkan():
        return Check(Level.OK, "GPU available for the models")
    return Check(
        Level.WARN,
        "No Vulkan: the models will use only the CPU and answer more slowly.",
        "Install your graphics card driver with Vulkan support.",
    )


def _ollama() -> Check:
    try:
        LocalHttpClient(default_ollama_url()).request("/api/tags", None, 3)
    except (ModelError, ValueError):
        return Check(
            Level.FAIL,
            f"SCRINIUM_MODEL_PROVIDER=ollama, but Ollama does not answer at "
            f"{default_ollama_url()}.",
            "Start Ollama or unset SCRINIUM_MODEL_PROVIDER to use llama.cpp.",
        )
    return Check(Level.OK, f"Ollama answers at {default_ollama_url()}")


def _ai_setup(data_dir: Path) -> Check:
    """The first-run choice (ADR-013), and whether a chosen local server answers."""
    # Imported here: the database is the heaviest import of the doctor.
    from scrinium.application.ai.setup import SetupChoice, managed_isolated, read_setup
    from scrinium.infrastructure.llm.switchable import local_server_chat
    from scrinium.infrastructure.sqlite.database import DATABASE_FILENAME, Database

    if managed_isolated():
        return Check(Level.OK, "Isolated mode, set by SCRINIUM_PROFILE: nothing leaves the machine")
    if not (data_dir / DATABASE_FILENAME).is_file():
        return Check(Level.OK, "First run: the included models are used until you choose others")
    try:
        database = Database.open(data_dir)
        with database.session() as session:
            setup = read_setup(session)
    except Exception:
        return Check(Level.WARN, "Could not read the model choice from the database.")
    if setup.choice is None:
        if setup.isolated:
            return Check(Level.WARN, "Isolated mode, with no answer model: rules cannot be written",
                         "Choose a local server in Settings > AI models.")  # fmt: skip
        return Check(Level.OK, "The answer model is not chosen yet: the console asks")
    if setup.choice is SetupChoice.BUNDLED:
        return Check(Level.OK, "Answer model: the bundled trial model")
    if setup.choice is SetupChoice.PROVIDER:
        return Check(Level.OK, "Model: your provider, with each project's consent")
    server = setup.local_server
    if server is None:
        return Check(Level.WARN, "Local server chosen, but with no address.")
    status = local_server_chat(server.url, server.model).status()
    if status.value != "ready":
        return Check(
            Level.WARN,
            f"The local server {server.url} does not answer with the model {server.model}.",
            "Start it, or change the choice in Settings > Model.",
        )
    return Check(Level.OK, f"Local server {server.url} with {server.model}")


def _window() -> Check:
    if window_available():
        return Check(Level.OK, "Scrinium's own window")
    if sys.platform.startswith("linux") and (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        fix = "Reinstall Scrinium with the window extra (the installer adds it on a desktop)."
        return Check(Level.WARN, "No own window: Scrinium will open the browser.", fix)
    return Check(Level.OK, "No desktop: Scrinium will use the browser")


def run_checks(data_dir: Path) -> list[Check]:
    data = _data_dir(data_dir)
    checks = [data]
    if data.level == Level.FAIL:
        return checks
    try:
        os_name, arch = current_platform()
    except ToolError as error:
        return [*checks, Check(Level.FAIL, str(error))]
    steps: list[Callable[[], Check | list[Check] | None]] = [
        lambda: _disk_encryption(data_dir),
        _keyring,
        lambda: _memory(locate_model(CHAT_MODEL, downloads.models_dir(data_dir)) is not None),
        lambda: _ai_setup(data_dir),
        lambda: _disk_space(data_dir),
        lambda: _tools(data_dir, os_name, arch),
    ]
    try:
        provider = default_model_provider()
    except ValueError:
        steps.append(lambda: Check(Level.FAIL, "SCRINIUM_MODEL_PROVIDER must be llama or ollama."))
    else:
        if provider == ModelProvider.OLLAMA:
            steps.append(_ollama)
        else:
            steps += [lambda: _llama_runs(data_dir, os_name, arch), lambda: _gpu(os_name)]
    steps.append(_window)
    for step in steps:
        result = step()
        if isinstance(result, list):
            checks += result
        elif result is not None:
            checks.append(result)
    return checks


def doctor(data_dir: Path) -> int:
    system = f"{platform.system()} {platform.machine()}"
    print(f"Scrinium {__version__}, {system}, Python {platform.python_version()}", flush=True)
    checks = run_checks(data_dir)
    for check in checks:
        print(f"[{check.level}] {check.message}", flush=True)
        if check.fix and check.level != Level.OK:
            print(f"        {check.fix}", flush=True)
    failed = any(check.level == Level.FAIL for check in checks)
    print("Fix what is marked as fail." if failed else "Ready to use Scrinium.")
    return 1 if failed else 0
