"""The little Git that `scrinium check` needs: what changed and what a file says now.

Local and read-only: `git diff`, `git ls-files`, `git ls-tree` and `git show`. Nothing fetches,
so nothing goes to the network; a base that is not in the clone is an error, never a download.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final

ADDED: Final = "A"
MODIFIED: Final = "M"
DELETED: Final = "D"
RENAMED: Final = "R"

# What the change is compared up to: the last commit, the working folder, or the index (what
# `git commit` is about to record, for the pre-commit hook).
COMMITTED: Final = "HEAD"
LOCAL: Final = "local"
STAGED: Final = "staged"
# A Reader's ref for the index: `git show :path`.
INDEX: Final = ""


class GitError(RuntimeError):
    pass


def git(cwd: Path, *args: str) -> str:
    try:
        done = subprocess.run(  # noqa: S603  git from the PATH, as the person runs it; no shell
            ["git", "-c", "core.quotePath=false", *args],  # noqa: S607
            cwd=cwd, capture_output=True, check=False, timeout=60,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(f"git is not available: {type(error).__name__}") from None
    if done.returncode != 0:
        message = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError(message[-1] if message else f"git {args[0]} failed")
    return done.stdout.decode("utf-8", "replace")


@dataclass
class Changes:
    """What the change does to the files, with paths relative to the repository's top level."""

    status: dict[str, str] = field(default_factory=dict)  # new path (or deleted path) -> A/M/D/R
    renames: dict[str, str] = field(default_factory=dict)  # old path -> new path

    @property
    def touched(self) -> set[str]:
        """Every path the change touches: the old side of a rename too."""
        return set(self.status) | set(self.renames)


@dataclass(frozen=True)
class Repository:
    top: Path
    # Where the source sits in the repository: "" at the top, "backend/" for a subfolder.
    prefix: str

    @classmethod
    def at(cls, root: Path) -> "Repository":
        top = Path(git(root, "rev-parse", "--show-toplevel").strip())
        prefix = git(root, "rev-parse", "--show-prefix").strip()
        return cls(top, prefix)

    def resolve(self, ref: str) -> str:
        try:
            return git(self.top, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
        except GitError:
            raise GitError(
                f'the base "{ref}" is not in this clone (in CI, clone with fetch-depth: 0)'
            ) from None

    def default_base(self) -> str:
        for ref in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
            try:
                self.resolve(ref)
            except GitError:
                continue
            return ref
        raise GitError("no base branch was found: give one with --base")

    def merge_base(self, base: str) -> str:
        try:
            return git(self.top, "merge-base", self.resolve(base), "HEAD").strip()
        except GitError:
            return self.resolve(base)

    def changes(self, since: str, head: str = COMMITTED) -> Changes:
        """Files changed from `since` to HEAD, to the working folder (untracked files included,
        ignored ones not), to the index, or to any other commit, branch or tag."""
        args = ["diff", "--name-status", "-M", "-z", "--no-ext-diff"]
        if head == STAGED:
            args.append("--cached")
        args.append(since)
        if head == COMMITTED:
            args.append("HEAD")
        elif head not in (LOCAL, STAGED):
            args.append(self.resolve(head))  # any commit, branch or tag (phase 7.1)
        fields = git(self.top, *args).split("\0")
        changes = Changes()
        index = 0
        while index < len(fields) and fields[index]:
            code = fields[index][0]
            if code == RENAMED:
                old, new = fields[index + 1], fields[index + 2]
                changes.renames[old] = new
                changes.status[new] = RENAMED
                index += 3
                continue
            if code == "C":  # Copied: the new file is an addition.
                changes.status[fields[index + 2]] = ADDED
                index += 3
                continue
            path = fields[index + 1]
            changes.status[path] = {ADDED: ADDED, DELETED: DELETED}.get(code, MODIFIED)
            index += 2
        if head == LOCAL:
            untracked = git(self.top, "ls-files", "--others", "--exclude-standard", "-z")
            for path in filter(None, untracked.split("\0")):
                changes.status.setdefault(path, ADDED)
        return changes

    def reader(self, head: str = COMMITTED) -> "Reader":
        named = {COMMITTED: "HEAD", LOCAL: None, STAGED: INDEX}
        return Reader(self, named[head] if head in named else self.resolve(head))


class Reader:
    """Reads files as they are at a commit, in the index (`ref` INDEX) or in the working
    folder (`ref` None)."""

    def __init__(self, repository: Repository, ref: str | None) -> None:
        self.repository = repository
        self.ref = ref

    def read(self, path: str) -> str | None:
        if self.ref is None:
            target = self.repository.top / path
            try:
                return target.read_text(encoding="utf-8", errors="replace")
            except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
                return None
        try:
            return git(self.repository.top, "show", f"{self.ref}:{path}")
        except GitError:
            return None

    def list_dir(self, directory: str) -> list[str]:
        """File names directly inside `directory` (relative to the top level)."""
        if self.ref is None:
            folder = self.repository.top / directory
            return (
                sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []
            )
        tree_dir = directory.rstrip("/") + "/"
        try:
            if self.ref == INDEX:
                listed = git(self.repository.top, "ls-files", "-z", "--", tree_dir)
                return sorted(
                    PurePosixPath(p).name
                    for p in listed.split("\0")
                    if p and PurePosixPath(p).parent.as_posix() + "/" == tree_dir
                )
            listed = git(self.repository.top, "ls-tree", "--name-only", "-z", self.ref, tree_dir)
        except GitError:
            return []
        return sorted(Path(p).name for p in listed.split("\0") if p)
