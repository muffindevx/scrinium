"""Reading a document folder without ever leaving it.

Each step is opened relative to its parent folder's descriptor and with O_NOFOLLOW: even if
someone swaps a folder for a symbolic link during the walk, reading never leaves the root.
"""

import os
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Final

_DIR_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def folders_supported() -> bool:
    return os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW")


class FolderEntryKind(StrEnum):
    FILE = "file"
    SYMLINK = "symlink"
    SPECIAL = "special"
    HARD_LINK = "hard_link"
    TOO_DEEP = "too_deep"
    SKIPPED_DIR = "skipped_dir"


@dataclass(frozen=True)
class FolderEntry:
    path: str
    kind: FolderEntryKind
    size: int = 0


class FolderChangedError(RuntimeError):
    """The file changed between the walk and the read."""


def walk(
    root: Path,
    skip_dir: Callable[[str], bool],
    max_depth: int,
    max_entries: int,
) -> Iterator[FolderEntry]:
    """Walks `root` without following links. `skip_dir` avoids entering excluded folders."""
    root_fd = os.open(root, _DIR_FLAGS)
    seen = 0
    stack: list[tuple[int, str, int]] = [(root_fd, "", 0)]
    try:
        while stack:
            dir_fd, prefix, depth = stack.pop()
            try:
                with os.scandir(dir_fd) as entries:
                    children = sorted(entries, key=lambda entry: entry.name)
                for child in children:
                    seen += 1
                    if seen > max_entries:
                        return
                    relative = f"{prefix}{child.name}"
                    info = child.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        yield FolderEntry(relative, FolderEntryKind.SYMLINK)
                    elif stat.S_ISDIR(info.st_mode):
                        if skip_dir(relative):
                            yield FolderEntry(relative, FolderEntryKind.SKIPPED_DIR)
                        elif depth + 1 >= max_depth:
                            yield FolderEntry(relative, FolderEntryKind.TOO_DEEP)
                        else:
                            child_fd = os.open(child.name, _DIR_FLAGS, dir_fd=dir_fd)
                            stack.append((child_fd, f"{relative}/", depth + 1))
                    elif not stat.S_ISREG(info.st_mode):
                        yield FolderEntry(relative, FolderEntryKind.SPECIAL)
                    elif info.st_nlink > 1:
                        # A hard link can be another name for a file outside.
                        yield FolderEntry(relative, FolderEntryKind.HARD_LINK)
                    else:
                        yield FolderEntry(relative, FolderEntryKind.FILE, info.st_size)
            finally:
                os.close(dir_fd)
    finally:
        for dir_fd, _, _ in stack:
            os.close(dir_fd)


def read_file(root: Path, relative: str, max_bytes: int) -> bytes:
    """Reads `relative` folder by folder without following links. Reads at most `max_bytes`+1."""
    path = PurePosixPath(relative)
    parts = path.parts
    # With an absolute path, os.open ignores dir_fd: it must be rejected first.
    if path.is_absolute() or not parts or any(part in {".", ".."} for part in parts):
        raise FolderChangedError
    fds = [os.open(root, _DIR_FLAGS)]
    try:
        for part in parts[:-1]:
            fds.append(os.open(part, _DIR_FLAGS, dir_fd=fds[-1]))
        fd = os.open(parts[-1], _FILE_FLAGS, dir_fd=fds[-1])
    except OSError:
        for opened in fds:
            os.close(opened)
        raise FolderChangedError from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            raise FolderChangedError
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read(max_bytes + 1)
    finally:
        os.close(fd)
        for opened in fds:
            os.close(opened)
