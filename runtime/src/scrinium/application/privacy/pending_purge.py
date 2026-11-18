"""A purge that failed after a confirmed deletion is retried at the next start (R3).

Deleting has two phases (`erasure`): the rows go in a transaction, then the disk is purged
(vectors, FTS5 segments, the WAL). If the second phase fails (the disk is full, the database is
locked), the rows are already gone but copies may remain in the files. What was left to do is
written to `pending-purge.json` in the data directory and done again when Scrinium starts, until
it succeeds. The file holds only ids, never content.
"""

import json
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger("scrinium.privacy")

PENDING_FILE: Final = "pending-purge.json"


def _read(data_dir: Path) -> list[dict[str, str]]:
    path = data_dir / PENDING_FILE
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in loaded if isinstance(item, dict)] if isinstance(loaded, list) else []


def _write(data_dir: Path, items: list[dict[str, str]]) -> None:
    path = data_dir / PENDING_FILE
    if not items:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(items), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def remember(data_dir: Path | None, kind: str, target: uuid.UUID | None = None) -> None:
    """Records a purge to do again: `source`, `project` (vectors of that id) or `all`."""
    if data_dir is None:
        return
    items = _read(data_dir)
    item = {"kind": kind, "id": str(target) if target else ""}
    if item not in items:
        items.append(item)
    _write(data_dir, items)


def pending(data_dir: Path) -> list[dict[str, str]]:
    return _read(data_dir)


def run_pending(data_dir: Path, steps: dict[str, Callable[[Any], None]]) -> int:
    """Does each pending purge again (`steps[kind](id)`); keeps those that fail. Returns how
    many are still pending."""
    left = []
    for item in _read(data_dir):
        step = steps.get(item.get("kind", ""))
        if step is None:
            continue
        try:
            step(uuid.UUID(item["id"]) if item.get("id") else None)
        except Exception:
            logger.warning("pending_purge_failed kind=%s", item.get("kind"))
            left.append(item)
    _write(data_dir, left)
    return len(left)
