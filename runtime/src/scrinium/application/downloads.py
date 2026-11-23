"""Moving the downloads from Settings (ADR-015): one move at a time, in its own thread.

A move copies GB between disks, so the request only starts it; the screen reads its progress
from the engine information. The old copy stays until the next start: the running llama-server
may still load a model from it.
"""

import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scrinium.infrastructure.tools import downloads
from scrinium.infrastructure.tools.installer import ToolError


@dataclass
class _Move:
    target: str
    done: int = 0
    total: int = 0
    state: str = "running"  # running, done, failed
    # Stable, for the console: a TargetError's code, "integrity" or "copy_failed".
    error: str | None = None
    detail: str | None = None


class DownloadsMover:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._lock = threading.Lock()
        self._move: _Move | None = None

    def start(self, target: Path) -> None:
        """Checks the folder now (ToolError with the reason) and copies in the background."""
        with self._lock:
            if self._move is not None and self._move.state == "running":
                raise downloads.TargetError("busy", "Ya se están moviendo las descargas.")
            resolved = downloads.check_target(self._data_dir, target)
            self._move = _Move(str(resolved))
            move = self._move
        threading.Thread(target=self._run, args=(move, resolved), daemon=True).start()

    def _run(self, move: _Move, target: Path) -> None:
        def progress(done: int, total: int) -> None:
            move.done, move.total = done, total

        try:
            downloads.move(self._data_dir, target, progress)
        except downloads.TargetError as error:
            move.state, move.error, move.detail = "failed", error.code, str(error)
        except downloads.IntegrityError as error:
            move.state, move.error, move.detail = "failed", "integrity", str(error)
        except (ToolError, OSError) as error:
            detail = error.strerror if isinstance(error, OSError) else str(error)
            move.state, move.error, move.detail = "failed", "copy_failed", detail
        else:
            move.state = "done"

    def info(self) -> dict[str, Any]:
        root = downloads.downloads_dir(self._data_dir)
        existing = root if root.is_dir() else self._data_dir
        move = self._move
        return {
            "dir": str(root),
            "data_dir": str(self._data_dir),
            "private": downloads.is_private(self._data_dir, root),
            "from_env": downloads.from_env(),
            "env_name": downloads.DOWNLOADS_ENV,
            "used_bytes": downloads.size_in_use(self._data_dir),
            "free_bytes": shutil.disk_usage(existing).free,
            "pending_cleanup": str(old)
            if (old := downloads.pending_cleanup(self._data_dir))
            else None,
            "move": None
            if move is None
            else {
                "target": move.target,
                "state": move.state,
                "done": move.done,
                "total": move.total,
                "error": move.error,
                "detail": move.detail,
            },
        }
