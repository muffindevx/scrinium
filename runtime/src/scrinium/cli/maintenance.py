"""`scrinium clean` and `scrinium uninstall`.

`clean` never touches projects, rules or questions: only leftovers (half-finished downloads,
tool and model versions no longer used, registry entries of stopped processes).

`uninstall` deletes everything: data, tools, models, keyring credentials and the program.
From the data directory it only deletes what Scrinium creates; anything else, it leaves and says so.
"""

import contextlib
import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

from scrinium.cli.control import stop
from scrinium.core import instances
from scrinium.infrastructure.llama_server import LLAMA_DIRNAME
from scrinium.infrastructure.secret_store import KeyringSecretStore
from scrinium.infrastructure.tools import downloads
from scrinium.infrastructure.tools.installer import MODELS_DIRNAME, TOOLS_DIRNAME
from scrinium.infrastructure.tools.manifest import MODELS, TOOLS

UNINSTALL_CONFIRMATION: Final = "DELETE"
_PARTIAL_DOWNLOAD: Final = ".descarga-"
_DATABASE: Final = "scrinium.sqlite3"
_OWNED_DIRS: Final = (
    TOOLS_DIRNAME,
    MODELS_DIRNAME,
    LLAMA_DIRNAME,
    instances.RUN_DIRNAME,
    "vectors",
    downloads.STATE_FILE,
)

Ask = Callable[[str], str]


def _size(path: Path) -> int:
    if path.is_symlink() or path.is_file():
        return path.lstat().st_size
    return sum(item.lstat().st_size for item in path.rglob("*") if not item.is_dir())


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024  # type: ignore[assignment]
    return f"{size} B"


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def leftovers(data_dir: Path) -> list[tuple[Path, str]]:
    """What `clean` can delete without losing anything in use, with the reason."""
    found: list[tuple[Path, str]] = []
    tools_dir = downloads.tools_dir(data_dir)
    for tool_dir in sorted(tools_dir.iterdir()) if tools_dir.is_dir() else []:
        tool = TOOLS.get(tool_dir.name)
        if tool is None:
            found.append((tool_dir, "tool no longer used"))
            continue
        for version_dir in sorted(tool_dir.iterdir()):
            if version_dir.name.startswith(_PARTIAL_DOWNLOAD):
                found.append((version_dir, "half-finished download"))
            elif not version_dir.name.startswith(f"{tool.version}-"):
                found.append((version_dir, f"old version of {tool.name}"))
    models_dir = downloads.models_dir(data_dir)
    for model_dir in sorted(models_dir.iterdir()) if models_dir.is_dir() else []:
        model = MODELS.get(model_dir.name)
        if model is None:
            found.append((model_dir, "model no longer used"))
            continue
        for revision_dir in sorted(model_dir.iterdir()):
            if revision_dir.name != model.revision:
                found.append((revision_dir, f"old version of {model.name}"))
                continue
            for item in sorted(revision_dir.iterdir()):
                if item.name.startswith(_PARTIAL_DOWNLOAD):
                    found.append((item, "half-finished download"))
                elif item.name != model.file:
                    found.append((item, "file that is not the model"))
    running = instances.running(data_dir)
    old = downloads.pending_cleanup(data_dir)
    if old is not None and not running:
        found += [
            (old / name, "earlier copy of the downloads, already moved")
            for name in (TOOLS_DIRNAME, MODELS_DIRNAME)
            if (old / name).exists()
        ]
    for instance in instances.recorded(data_dir):
        if instance not in running:
            found.append(
                (
                    data_dir / instances.RUN_DIRNAME / f"{instance.port}.json",
                    "process already stopped",
                )
            )
    llama_dir = data_dir / LLAMA_DIRNAME
    if llama_dir.is_dir() and not running:
        found.append((llama_dir, "temporary llama-server configuration"))
    return found


def _confirmed(question: str, assume_yes: bool, ask: Ask) -> bool:
    if assume_yes:
        return True
    try:
        return ask(question).strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def clean(data_dir: Path, assume_yes: bool, ask: Ask = input) -> int:
    downloads.forget_missing(data_dir)
    found = leftovers(data_dir)
    if not found:
        print("Nothing to clean.")
        return 0
    total = 0
    for path, reason in found:
        size = _size(path)
        total += size
        shown = path.relative_to(data_dir) if path.is_relative_to(data_dir) else path
        print(f"  {shown}  ({_human(size)}): {reason}")
    print(f"Frees {_human(total)}. Your projects, rules and questions are not touched.")
    if not _confirmed("Delete? [y/N] ", assume_yes, ask):
        print("Cancelled: nothing was deleted.")
        return 1
    for path, _ in found:
        _remove(path)
    downloads.forget_missing(data_dir)
    if downloads.pending_cleanup(data_dir) is not None and not instances.running(data_dir):
        downloads.finish_cleanup(data_dir)
    print("Clean.")
    return 0


def _secret_refs(data_dir: Path) -> list[str]:
    """Connector credentials in the keyring, read without migrating or opening the runtime."""
    database = data_dir / _DATABASE
    if not database.is_file():
        return []
    try:
        with contextlib.closing(
            sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        ) as connection:
            rows = connection.execute(
                "SELECT secret_ref FROM source_connections WHERE secret_ref IS NOT NULL"
            ).fetchall()
    except sqlite3.Error:
        return []
    return [str(row[0]) for row in rows]


def _owned(data_dir: Path) -> list[Path]:
    if not data_dir.is_dir():
        return []
    owned = [
        item
        for item in sorted(data_dir.iterdir())
        if item.name in _OWNED_DIRS or item.name.startswith(_DATABASE)
    ]
    # Downloads moved elsewhere (ADR-015): only Scrinium's own folders there, never the rest.
    roots = {downloads.downloads_dir(data_dir), downloads.pending_cleanup(data_dir)}
    for root in sorted(r for r in roots if r is not None and not downloads.is_private(data_dir, r)):
        owned += [root / name for name in (TOOLS_DIRNAME, MODELS_DIRNAME) if (root / name).exists()]
    return owned


def _uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv")
    return str(local) if local.is_file() else None


def _installed_with_uv(uv: str) -> bool:
    """Whether this Scrinium is the one from `uv tool install` (and not a source checkout)."""
    try:
        result = subprocess.run(  # noqa: S603  system uv, no shell
            [uv, "tool", "dir"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    tools_dir = result.stdout.strip()
    return bool(tools_dir) and Path(sys.prefix).resolve().is_relative_to(Path(tools_dir).resolve())


def _uninstall_program() -> str:
    uv = _uv()
    if uv is None or not _installed_with_uv(uv):
        return "The program runs from the source code, not from uv: it is not uninstalled."
    if sys.platform == "win32":
        # Windows cannot delete the running executable: uv does it as soon as this process ends.
        subprocess.Popen(  # noqa: S603  system cmd with fixed arguments
            [
                str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "cmd.exe"),
                "/c", "timeout", "/t", "3", ">nul", "&", uv, "tool", "uninstall", "scrinium",
            ],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore[attr-defined]
            close_fds=True,
        )  # fmt: skip
        return "The program is uninstalled a few seconds after this command ends."
    result = subprocess.run(  # noqa: S603  system uv, no shell
        [uv, "tool", "uninstall", "scrinium"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return "uv could not uninstall the program. Do it with: uv tool uninstall scrinium"
    return "Program uninstalled."


def uninstall(data_dir: Path, assume_yes: bool, ask: Ask = input) -> int:
    owned = _owned(data_dir)
    secrets = _secret_refs(data_dir)
    size = sum(_size(item) for item in owned)
    print("All of Scrinium will be removed from this machine:")
    print(f"  - Data, tools and models in {data_dir} ({_human(size)})")
    elsewhere = sorted({item.parent for item in owned if not item.is_relative_to(data_dir)})
    for root in elsewhere:
        print(f"  - Tools and models moved to {root} (only its tools and models folders)")
    print(f"  - Connector credentials in the keyring: {len(secrets)}")
    print("  - The scrinium program")
    print("uv stays: other programs may use it. Exports you downloaded are not touched.")
    if not assume_yes:
        try:
            answer = ask(f"Type {UNINSTALL_CONFIRMATION} to continue: ")
        except EOFError:
            answer = ""
        if answer.strip() != UNINSTALL_CONFIRMATION:
            print("Cancelled: nothing was deleted.")
            return 1
    stop(data_dir)
    store = KeyringSecretStore.from_system()
    for ref in secrets:
        with contextlib.suppress(Exception):
            store.delete(ref)
    for item in owned:
        _remove(item)
    remaining = sorted(data_dir.iterdir()) if data_dir.is_dir() else []
    if data_dir.is_dir() and not remaining:
        data_dir.rmdir()
    elif remaining:
        names = ", ".join(item.name for item in remaining)
        print(f"Files in {data_dir} that are not Scrinium's were left: {names}")
    print("Data deleted.")
    print(_uninstall_program())
    return 0
