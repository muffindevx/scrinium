"""Where Scrinium keeps what it downloads: llama.cpp, gitleaks and the models (ADR-015).

By default, inside the data directory (`tools/` and `models/`). A person can move them to
another folder, for example a larger disk. The data directory cannot follow: it holds the text
of the sources and must stay on a disk that keeps permissions. Downloads are public files
checked against the manifest's SHA-256, so they can live on a disk that does not (NTFS, exFAT).

The choice is kept in `<data dir>/downloads.json`, next to an integrity record of every file
(size, modification time and SHA-256, or the target of a link). The record stays in the data
directory on purpose: outside it another account might change the files, and it must not be
able to change the record as well. `SCRINIUM_DOWNLOADS_DIR` overrides the choice.

Outside the data directory, before llama-server starts:

- every file of the tool (the executable and its libraries) is hashed in full: about 100 MB,
  well under a second;
- a model is hashed again only if its size or modification time changed, since it weighs GB.
  `scrinium doctor` hashes them all.

Inside the data directory nothing is checked: it is private to the account, like the database.
"""

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from scrinium.infrastructure.tools.installer import MODELS_DIRNAME, TOOLS_DIRNAME, ToolError
from scrinium.infrastructure.tools.manifest import MODELS, TOOLS

DOWNLOADS_ENV: Final = "SCRINIUM_DOWNLOADS_DIR"
STATE_FILE: Final = "downloads.json"
_PARTIAL: Final = ".descarga-"
_CHUNK: Final = 1024 * 1024
# Room left free on the target disk after a move: a full disk breaks more than the move.
_SPARE_BYTES: Final = 512 * 1024 * 1024

Progress = Callable[[int, int], None]


class IntegrityError(ToolError):
    """A downloaded file is not the one installed. The message is for the end user."""


class TargetError(ToolError):
    """A folder the downloads cannot move to. `code` is stable, for the API and the console."""

    def __init__(self, code: str, message: str, needed_bytes: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.needed_bytes = needed_bytes


@dataclass(frozen=True)
class Entry:
    size: int
    mtime_ns: int
    sha256: str | None = None
    # A symbolic link records where it points instead of a hash.
    link: str | None = None


# --- the state file -----------------------------------------------------------------------


def _state_path(data_dir: Path) -> Path:
    return data_dir / STATE_FILE


def _load(data_dir: Path) -> dict[str, Any]:
    try:
        state = json.loads(_state_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"dir": None, "files": {}, "cleanup": None}
    if not isinstance(state, dict):
        return {"dir": None, "files": {}, "cleanup": None}
    files = state.get("files")
    return {
        "dir": state.get("dir") if isinstance(state.get("dir"), str) else None,
        "files": files if isinstance(files, dict) else {},
        "cleanup": state.get("cleanup") if isinstance(state.get("cleanup"), str) else None,
    }


def _save(data_dir: Path, state: dict[str, Any]) -> None:
    """Atomic and private: a crash leaves the old file or the new one, never half of one."""
    handle, name = tempfile.mkstemp(dir=data_dir, prefix=".downloads-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(state, output, indent=1, sort_keys=True)
        os.chmod(name, 0o600)
        os.replace(name, _state_path(data_dir))
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


# --- where ----------------------------------------------------------------------------------


def from_env() -> bool:
    return bool(os.environ.get(DOWNLOADS_ENV))


def downloads_dir(data_dir: Path) -> Path:
    """The folder holding `tools/` and `models/`: the variable, the chosen one, or the data dir."""
    override = os.environ.get(DOWNLOADS_ENV)
    if override:
        return Path(override).expanduser()
    chosen = _load(data_dir)["dir"]
    return Path(chosen) if chosen else data_dir


def tools_dir(data_dir: Path) -> Path:
    return downloads_dir(data_dir) / TOOLS_DIRNAME


def models_dir(data_dir: Path) -> Path:
    return downloads_dir(data_dir) / MODELS_DIRNAME


def is_private(data_dir: Path, root: Path | None = None) -> bool:
    """Whether the downloads live in the data directory, where only this account writes."""
    root = downloads_dir(data_dir) if root is None else root
    try:
        return root.resolve() == data_dir.resolve()
    except OSError:
        return False


def pending_cleanup(data_dir: Path) -> Path | None:
    """The previous folder of a move, still to delete: llama-server may be reading it."""
    cleanup = _load(data_dir)["cleanup"]
    return Path(cleanup) if cleanup else None


# --- integrity ------------------------------------------------------------------------------


def _hash(path: Path, progress: Callable[[int], None] | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK):
            digest.update(chunk)
            if progress is not None:
                progress(len(chunk))
    return digest.hexdigest()


def matches(path: Path, sha256: str) -> bool:
    return path.is_file() and not path.is_symlink() and _hash(path) == sha256


def _files(path: Path) -> Iterator[Path]:
    """The files and links of a tree (or the path itself), without following links."""
    if path.is_symlink() or path.is_file():
        yield path
        return
    for folder, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if not name.startswith(_PARTIAL))
        for name in sorted(filenames) + [d for d in dirnames if (Path(folder) / d).is_symlink()]:
            yield Path(folder) / name


def _key(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _entry(path: Path, sha256: str | None = None) -> Entry:
    info = path.lstat()
    if path.is_symlink():
        return Entry(info.st_size, info.st_mtime_ns, link=os.readlink(path))
    return Entry(info.st_size, info.st_mtime_ns, sha256 or _hash(path))


def record(data_dir: Path, *paths: Path) -> None:
    """Records what was just installed in the downloads folder, as it is now on disk.
    Nothing to record in the data directory: nothing is checked there."""
    root = downloads_dir(data_dir)
    if is_private(data_dir, root):
        return
    state = _load(data_dir)
    for path in paths:
        prefix = _key(root, path)
        state["files"] = {
            key: value
            for key, value in state["files"].items()
            if key != prefix and not key.startswith(prefix + "/")
        }
        for item in _files(path):
            state["files"][_key(root, item)] = _entry(item).__dict__
    _save(data_dir, state)


def has_record(data_dir: Path, path: Path) -> bool:
    root = downloads_dir(data_dir)
    prefix = _key(root, path)
    files = _load(data_dir)["files"]
    return any(key == prefix or key.startswith(prefix + "/") for key in files)


def _recorded(raw: object) -> Entry | None:
    if not isinstance(raw, dict):
        return None
    try:
        return Entry(**raw)
    except TypeError:
        return None


def verify(data_dir: Path, path: Path, *, full: bool) -> None:
    """Raises IntegrityError if `path` (a model, or a tool's folder) is not what was installed.

    `full` hashes every file; otherwise a file whose size and modification time are unchanged
    is taken as is. A file that appeared in the tree since is an error too: a library dropped
    next to llama-server would be loaded by it.
    """
    root = downloads_dir(data_dir)
    if is_private(data_dir, root):
        return
    state = _load(data_dir)
    prefix = _key(root, path)
    expected = {
        key: entry
        for key, raw in state["files"].items()
        if (key == prefix or key.startswith(prefix + "/")) and (entry := _recorded(raw))
    }
    if not expected:
        raise IntegrityError(
            f"{path} no tiene registro de integridad. Vuelve a instalarlo con: "
            "scrinium tools install"
        )
    seen = set()
    changed = False
    for item in _files(path):
        key = _key(root, item)
        seen.add(key)
        known = expected.get(key)
        if known is None:
            raise IntegrityError(f"{item} no lo instaló Scrinium: se deja sin usar.")
        if known.link is not None:
            if not item.is_symlink() or os.readlink(item) != known.link:
                raise IntegrityError(f"El enlace {item} cambió desde que se instaló.")
            continue
        if item.is_symlink():
            raise IntegrityError(f"{item} cambió desde que se instaló.")
        info = item.stat()
        if not full and info.st_size == known.size and info.st_mtime_ns == known.mtime_ns:
            continue
        if info.st_size != known.size or _hash(item) != known.sha256:
            raise IntegrityError(f"{item} cambió desde que se instaló: se deja sin usar.")
        # Same bytes, new time (a copy, a backup tool): the next start need not hash it again.
        state["files"][key] = Entry(info.st_size, info.st_mtime_ns, known.sha256).__dict__
        changed = True
    missing = sorted(set(expected) - seen)
    if missing:
        raise IntegrityError(
            f"Falta {root / missing[0]}. Vuelve a instalarlo con: scrinium tools install"
        )
    if changed:
        _save(data_dir, state)


# --- moving ---------------------------------------------------------------------------------


def _in_use(root: Path) -> list[Path]:
    """What the manifest uses today in `root`: the current version of each tool and model.
    Old versions and half downloads are not moved; they go with the old folder."""
    found: list[Path] = []
    for tool in TOOLS.values():
        folder = root / TOOLS_DIRNAME / tool.name
        if folder.is_dir():
            found += sorted(
                item
                for item in folder.iterdir()
                if item.is_dir() and item.name.startswith(f"{tool.version}-")
            )
    for model in MODELS.values():
        file = root / MODELS_DIRNAME / model.name / model.revision / model.file
        if file.is_file():
            found.append(file)
    return found


def size_in_use(data_dir: Path) -> int:
    root = downloads_dir(data_dir)
    return sum(item.lstat().st_size for path in _in_use(root) for item in _files(path))


def check_target(data_dir: Path, target: Path) -> Path:
    """The folder to move to, resolved; TargetError with the reason if it cannot be used."""
    if from_env():
        raise TargetError(
            "from_env",
            f"La carpeta la fija la variable {DOWNLOADS_ENV}: cámbiala ahí o quítala para elegir "
            "otra desde Scrinium.",
        )
    if not target.is_absolute():
        raise TargetError("not_absolute", "Escribe la ruta completa de la carpeta, desde la raíz.")
    target = target.resolve()
    if target == downloads_dir(data_dir).resolve():
        raise TargetError("same_dir", "Las descargas ya están en esa carpeta.")
    data = data_dir.resolve()
    if target != data and (target.is_relative_to(data) or data.is_relative_to(target)):
        raise TargetError(
            "inside_data_dir",
            "Elige una carpeta fuera de la carpeta de datos de Scrinium, o ella misma.",
        )
    if target.exists() and not target.is_dir():
        raise TargetError("not_a_dir", "Esa ruta es un archivo, no una carpeta.")
    parent = target if target.exists() else target.parent
    if not parent.is_dir():
        raise TargetError("no_parent", "La carpeta que la contiene no existe.")
    if not os.access(parent, os.W_OK | os.X_OK):
        raise TargetError("not_writable", "Scrinium no puede escribir en esa carpeta.")
    for name in (TOOLS_DIRNAME, MODELS_DIRNAME):
        existing = target / name
        if target != data and existing.exists() and any(existing.iterdir()):
            raise TargetError(
                "not_empty",
                f"{existing} ya tiene archivos. Elige una carpeta vacía o bórrala antes: Scrinium "
                "no mezcla sus descargas con archivos que no instaló.",
            )
    needed = size_in_use(data_dir) + _SPARE_BYTES
    if shutil.disk_usage(parent).free < needed:
        raise TargetError(
            "no_space",
            f"No hay espacio en ese disco: hacen falta {needed / 1e9:.1f} GB libres.",
            needed,
        )
    return target


def _copy(source: Path, target: Path, progress: Callable[[int], None]) -> str:
    """Copies a file and returns the SHA-256 of what landed on disk, read back from it."""
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with source.open("rb") as reader, target.open("wb") as writer:
        while chunk := reader.read(_CHUNK):
            writer.write(chunk)
    shutil.copystat(source, target)
    return _hash(target, progress)


def move(data_dir: Path, target: Path, progress: Progress | None = None) -> None:
    """Copies what is in use to `target`, checks every byte, then switches to it.

    The old copy is not deleted here: a running llama-server may load a model from it later.
    It is recorded as pending and deleted with `finish_cleanup`, by the next start or by the
    CLI when Scrinium is not running. Nothing changes if any step fails.
    """
    target = check_target(data_dir, target)
    source_root = downloads_dir(data_dir)
    in_use = _in_use(source_root)
    # The source is trusted only as recorded: outside the data directory it is checked first.
    for path in in_use:
        verify(data_dir, path, full=True)
    # Links are recreated, not copied: only files count towards the progress.
    total = sum(
        item.stat().st_size for path in in_use for item in _files(path) if not item.is_symlink()
    )
    done = 0

    def advance(count: int) -> None:
        nonlocal done
        done += count
        if progress is not None:
            progress(done, total * 2)

    files: dict[str, dict[str, Any]] = {}
    created: list[Path] = []
    try:
        for path in in_use:
            for item in _files(path):
                key = _key(source_root, item)
                destination = target / key
                if item.is_symlink():
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    destination.symlink_to(os.readlink(item))
                    created.append(destination)
                    files[key] = _entry(destination).__dict__
                    continue
                source_hash = _hash(item, advance)
                created.append(destination)
                if _copy(item, destination, advance) != source_hash:
                    raise IntegrityError(f"La copia de {item.name} no coincide: no se mueve nada.")
                files[key] = _entry(destination, source_hash).__dict__
    except BaseException:
        if target == data_dir.resolve():
            # The data directory may still hold the files of an earlier move: only ours go.
            for path in created:
                path.unlink(missing_ok=True)
        else:
            # check_target made sure these were empty: everything in them is this copy.
            for name in (TOOLS_DIRNAME, MODELS_DIRNAME):
                shutil.rmtree(target / name, ignore_errors=True)
        raise
    state = _load(data_dir)
    state["files"] = files
    state["dir"] = None if target == data_dir.resolve() else str(target)
    state["cleanup"] = None if source_root.resolve() == target else str(source_root)
    _save(data_dir, state)


def finish_cleanup(data_dir: Path) -> Path | None:
    """Deletes the old copy of a finished move, if any. Returns the folder it emptied."""
    old = pending_cleanup(data_dir)
    if old is None:
        return None
    for name in (TOOLS_DIRNAME, MODELS_DIRNAME):
        folder = old / name
        if folder.resolve() != (downloads_dir(data_dir) / name).resolve():
            shutil.rmtree(folder, ignore_errors=True)
    state = _load(data_dir)
    state["cleanup"] = None
    _save(data_dir, state)
    return old


def share_downloads(source_data_dir: Path, data_dir: Path) -> None:
    """Another data directory (the demo's) uses the same downloads and the same record."""
    state = _load(source_data_dir)
    state["dir"] = str(downloads_dir(source_data_dir))
    state["cleanup"] = None
    _save(data_dir, state)
