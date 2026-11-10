"""Which files of a source would be indexed, and why the rest are excluded.

Preview and sync use this same plan: what you see is what gets indexed.
"""

import hashlib
import os
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from scrinium.application.ingestion.policy import (
    MAX_DEPTH,
    MAX_FILE_BYTES,
    MAX_INCLUDED_BYTES,
    MAX_INCLUDED_FILES,
    MAX_SCANNED_ENTRIES,
    Exclusion,
    ExclusionReason,
    SourceKind,
    check_content,
    check_path,
    skip_directory,
)
from scrinium.infrastructure.local_files import folder_reader
from scrinium.infrastructure.local_files.folder_reader import FolderChangedError, FolderEntryKind
from scrinium.infrastructure.local_files.git_reader import (
    EntryKind,
    GitRepository,
)


class SourceErrorCode(StrEnum):
    PATH_NOT_ABSOLUTE = "path_not_absolute"
    PATH_NOT_FOUND = "path_not_found"
    NOT_A_DIRECTORY = "not_a_directory"
    WRONG_OWNER = "wrong_owner"
    CONTAINS_SCRINIUM_DATA = "contains_scrinium_data"
    FOLDERS_UNSUPPORTED = "folders_unsupported"


class SourceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class IncludedFile:
    path: str
    size: int
    # Git: blob hash. Folder: SHA-256 of the content. Same key = same revision.
    revision_key: str
    content_hash: str
    text: str


@dataclass(frozen=True)
class ExcludedFile:
    path: str
    exclusion: Exclusion


@dataclass
class Plan:
    kind: SourceKind
    root: Path
    commit: str | None
    included: list[IncludedFile] = field(default_factory=list)
    excluded: list[ExcludedFile] = field(default_factory=list)
    included_bytes: int = 0

    def exclude(self, path: str, exclusion: Exclusion) -> None:
        self.excluded.append(ExcludedFile(path, exclusion))

    def has_room(self, size: int) -> bool:
        return (
            len(self.included) < MAX_INCLUDED_FILES
            and self.included_bytes + size <= MAX_INCLUDED_BYTES
        )

    def include(self, path: str, revision_key: str, text: str, size: int) -> None:
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        self.included.append(IncludedFile(path, size, revision_key, content_hash, text))
        self.included_bytes += size

    def excluded_by_reason(self) -> dict[str, int]:
        return dict(Counter(item.exclusion.reason.value for item in self.excluded))


def resolve_root(kind: SourceKind, raw_path: str, data_dir: Path) -> Path:
    """Validates the folder the person chose. It reads nothing inside it."""
    path = Path(raw_path)
    if not path.is_absolute():
        raise SourceError(SourceErrorCode.PATH_NOT_ABSOLUTE)
    if not path.exists():
        raise SourceError(SourceErrorCode.PATH_NOT_FOUND)
    root = path.resolve()
    if not root.is_dir():
        raise SourceError(SourceErrorCode.NOT_A_DIRECTORY)
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and root.stat().st_uid != geteuid():
        raise SourceError(SourceErrorCode.WRONG_OWNER)
    data = data_dir.resolve()
    # Scrinium does not index itself: the database would end up inside its own index.
    if data.is_relative_to(root) or root.is_relative_to(data):
        raise SourceError(SourceErrorCode.CONTAINS_SCRINIUM_DATA)
    if kind is SourceKind.FOLDER and not folder_reader.folders_supported():
        raise SourceError(SourceErrorCode.FOLDERS_UNSUPPORTED)
    return root


def _add_content(plan: Plan, path: str, data: bytes, revision_key: str | None) -> None:
    text, exclusion = check_content(data)
    if exclusion is not None or text is None:
        plan.exclude(path, exclusion or Exclusion(ExclusionReason.BINARY))
        return
    if not plan.has_room(len(data)):
        plan.exclude(path, Exclusion(ExclusionReason.LIMIT_REACHED))
        return
    key = revision_key or hashlib.sha256(data).hexdigest()
    plan.include(path, key, text, len(data))


_GIT_KIND_EXCLUSIONS = {
    EntryKind.SYMLINK: Exclusion(ExclusionReason.SYMLINK),
    EntryKind.SUBMODULE: Exclusion(ExclusionReason.SUBMODULE),
}


def _git_plan(root: Path, user_patterns: list[str]) -> Plan:
    with GitRepository(root) as repository:
        repository.check_root()
        commit = repository.head()
        plan = Plan(SourceKind.GIT_REPOSITORY, root, commit)
        to_read: dict[str, list[str]] = {}
        for entry in repository.tree(commit)[:MAX_SCANNED_ENTRIES]:
            path = entry.path
            if path is None:
                plan.exclude(
                    entry.raw_path.decode("utf-8", "replace"), Exclusion(ExclusionReason.BAD_PATH)
                )
                continue
            # First what the entry is: a link is not an "unsupported type".
            exclusion = _GIT_KIND_EXCLUSIONS.get(entry.kind) or check_path(
                path, SourceKind.GIT_REPOSITORY, user_patterns
            )
            if exclusion is None and (entry.size or 0) > MAX_FILE_BYTES:
                exclusion = Exclusion(ExclusionReason.TOO_LARGE)
            if exclusion is not None:
                plan.exclude(path, exclusion)
            else:
                to_read.setdefault(entry.blob, []).append(path)
        for blob, data in repository.read_blobs(list(to_read)):
            for path in to_read[blob]:
                _add_content(plan, path, data, blob)
    plan.included.sort(key=lambda item: item.path)
    return plan


_FOLDER_EXCLUSIONS = {
    FolderEntryKind.SYMLINK: ExclusionReason.SYMLINK,
    FolderEntryKind.SPECIAL: ExclusionReason.SPECIAL_FILE,
    FolderEntryKind.HARD_LINK: ExclusionReason.HARD_LINK,
    FolderEntryKind.TOO_DEEP: ExclusionReason.TOO_DEEP,
}


def _folder_plan(root: Path, user_patterns: list[str]) -> Plan:
    plan = Plan(SourceKind.FOLDER, root, None)
    entries = folder_reader.walk(
        root,
        skip_dir=lambda relative: skip_directory(relative) is not None,
        max_depth=MAX_DEPTH,
        max_entries=MAX_SCANNED_ENTRIES,
    )
    for entry in entries:
        if entry.kind is FolderEntryKind.SKIPPED_DIR:
            plan.exclude(
                f"{entry.path}/",
                skip_directory(entry.path) or Exclusion(ExclusionReason.DEFAULT_PATH),
            )
            continue
        if entry.kind in _FOLDER_EXCLUSIONS:
            exclusion: Exclusion | None = Exclusion(_FOLDER_EXCLUSIONS[entry.kind])
        else:
            exclusion = check_path(entry.path, SourceKind.FOLDER, user_patterns)
        if exclusion is None and entry.size > MAX_FILE_BYTES:
            exclusion = Exclusion(ExclusionReason.TOO_LARGE)
        if exclusion is not None:
            plan.exclude(entry.path, exclusion)
            continue
        try:
            data = folder_reader.read_file(root, entry.path, MAX_FILE_BYTES)
        except FolderChangedError:
            plan.exclude(entry.path, Exclusion(ExclusionReason.SPECIAL_FILE, "changed"))
            continue
        _add_content(plan, entry.path, data, None)
    plan.included.sort(key=lambda item: item.path)
    return plan


def build_plan(kind: SourceKind, root: Path, user_patterns: list[str]) -> Plan:
    """Raises `GitError` if the repository cannot be read."""
    if kind is SourceKind.GIT_REPOSITORY:
        return _git_plan(root, user_patterns)
    return _folder_plan(root, user_patterns)
