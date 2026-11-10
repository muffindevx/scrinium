"""Reading a Git repository without changing it or running anything of its own.

Only read commands are used (`rev-parse`, `ls-tree`, `cat-file`, `for-each-ref`, `log`
without patches and `diff-tree` with file names only), which run no hooks, filters, fsmonitor,
textconv or external diff. The environment is built from scratch: none of the person's
GIT_* variables, no global or system configuration, and no network.
"""

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

_TIMEOUT_SECONDS: Final = 120
_MODE_FILE: Final = frozenset({"100644", "100755"})
_MODE_SYMLINK: Final = "120000"
_MODE_SUBMODULE: Final = "160000"
# SHA-1 or SHA-256. `cat-file --batch` accepts any expression: only hashes are passed.
_OBJECT_ID: Final = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")

# They override repository settings that could run programs or open connections.
_SAFE_CONFIG: Final = (
    "core.fsmonitor=false",
    "core.hooksPath=" + os.devnull,
    "core.sshCommand=false",
    "protocol.allow=never",
    "credential.helper=",
)


class GitErrorCode(StrEnum):
    UNAVAILABLE = "git_unavailable"
    NOT_A_REPOSITORY = "not_a_git_repository"
    NOT_REPOSITORY_ROOT = "not_repository_root"
    EMPTY_REPOSITORY = "empty_repository"
    FAILED = "git_failed"


class GitError(RuntimeError):
    def __init__(self, code: GitErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class EntryKind(StrEnum):
    FILE = "file"
    SYMLINK = "symlink"
    SUBMODULE = "submodule"


@dataclass(frozen=True)
class Tag:
    name: str
    commit: str
    # Date of the annotated tag or, if lightweight, of the commit.
    created_at: int


@dataclass(frozen=True)
class Commit:
    sha: str
    committed_at: int
    subject: str
    parents: tuple[str, ...]


@dataclass(frozen=True)
class ChangedFile:
    status: str  # A, M, D or T, without rename detection
    path: str


@dataclass(frozen=True)
class TreeEntry:
    path: str | None  # None if the path is not valid UTF-8
    raw_path: bytes
    kind: EntryKind
    blob: str
    size: int | None


def _environment(home: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": home,
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "GIT_OPTIONAL_LOCKS": "0",
        # A partial clone would download the missing objects: nothing ever goes out to the network.
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }


class GitRepository:
    def __init__(self, root: Path) -> None:
        executable = shutil.which("git")
        if executable is None:
            raise GitError(GitErrorCode.UNAVAILABLE)
        self.root = root
        self._git = executable
        # HOME vacio: ni ~/.gitconfig ni ~/.config/git llegan a leerse.
        self._home = tempfile.mkdtemp(prefix="scrinium-git-")
        self._env = _environment(self._home)

    def close(self) -> None:
        shutil.rmtree(self._home, ignore_errors=True)

    def __enter__(self) -> "GitRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _command(self, *args: str) -> list[str]:
        command = [self._git, "-C", str(self.root)]
        for setting in _SAFE_CONFIG:
            command += ["-c", setting]
        return [*command, *args]

    def _run(self, *args: str) -> bytes:
        try:
            result = subprocess.run(  # noqa: S603  fixed arguments, no shell
                self._command(*args),
                capture_output=True,
                env=self._env,
                timeout=_TIMEOUT_SECONDS,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            raise GitError(GitErrorCode.FAILED) from None
        if result.returncode != 0:
            raise GitError(GitErrorCode.FAILED)
        return result.stdout

    def check_root(self) -> None:
        """Requires the root of a repository: a subfolder would give ambiguous relative paths."""
        try:
            toplevel = self._run("rev-parse", "--show-toplevel").decode().strip()
        except (GitError, UnicodeDecodeError):
            raise GitError(GitErrorCode.NOT_A_REPOSITORY) from None
        if not toplevel or Path(toplevel).resolve() != self.root.resolve():
            raise GitError(GitErrorCode.NOT_REPOSITORY_ROOT)

    def head(self) -> str:
        try:
            output = self._run("rev-parse", "--verify", "--end-of-options", "HEAD^{commit}")
        except GitError:
            raise GitError(GitErrorCode.EMPTY_REPOSITORY) from None
        return output.decode("ascii").strip()

    def tree(self, commit: str) -> list[TreeEntry]:
        output = self._run("ls-tree", "-r", "-l", "-z", "--full-tree", "--end-of-options", commit)
        entries = []
        for record in output.split(b"\x00"):
            if not record:
                continue
            meta, _, raw_path = record.partition(b"\t")
            mode, _type, blob, size = meta.decode("ascii").split()
            if mode in _MODE_FILE:
                kind = EntryKind.FILE
            elif mode == _MODE_SYMLINK:
                kind = EntryKind.SYMLINK
            elif mode == _MODE_SUBMODULE:
                kind = EntryKind.SUBMODULE
            else:
                continue
            try:
                path: str | None = raw_path.decode("utf-8")
            except UnicodeDecodeError:
                path = None
            entries.append(
                TreeEntry(path, raw_path, kind, blob, None if size == "-" else int(size))
            )
        return entries

    def read_blobs(self, blobs: Iterable[str]) -> Iterator[tuple[str, bytes]]:
        """Reads raw blobs with `cat-file --batch`: no filters or conversions."""
        process = subprocess.Popen(  # noqa: S603  fixed arguments, no shell
            self._command("cat-file", "--batch"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=self._env,
        )
        stdin, stdout = process.stdin, process.stdout
        if stdin is None or stdout is None:
            raise GitError(GitErrorCode.FAILED)
        try:
            for blob in blobs:
                if not _OBJECT_ID.match(blob):
                    raise GitError(GitErrorCode.FAILED)
                stdin.write(blob.encode("ascii") + b"\n")
                stdin.flush()
                header = stdout.readline().decode("ascii").split()
                if len(header) != 3 or header[1] != "blob":
                    raise GitError(GitErrorCode.FAILED)
                data = stdout.read(int(header[2]))
                stdout.read(1)  # trailing newline
                yield blob, data
        finally:
            stdin.close()
            process.kill()
            process.wait(timeout=_TIMEOUT_SECONDS)

    def tags(self) -> list[Tag]:
        """Tags pointing to a commit. Those that are not valid UTF-8 or point to something else
        (a tree, a blob) are ignored."""
        output = self._run(
            "for-each-ref",
            "--format=%(refname:strip=2)%00%(objectname)%00%(*objectname)%00%(objecttype)"
            "%00%(*objecttype)%00%(creatordate:unix)",
            "refs/tags",
        )
        tags = []
        for line in output.split(b"\n"):
            fields = line.split(b"\x00")
            if len(fields) != 6:
                continue
            try:
                name, direct, peeled, kind, peeled_kind, date = (f.decode() for f in fields)
            except UnicodeDecodeError:
                continue
            commit = direct if kind == "commit" else peeled if peeled_kind == "commit" else ""
            if _OBJECT_ID.match(commit) and date.isdigit():
                tags.append(Tag(name, commit, int(date)))
        return tags

    def log(self, revision: str, limit: int) -> list[Commit]:
        """The last `limit` commits up to `revision`, from newest to oldest."""
        if not _OBJECT_ID.match(revision):
            raise GitError(GitErrorCode.FAILED)
        output = self._run(
            "log", "--no-color", "--no-show-signature", f"--max-count={limit}",
            "--format=%H%x1f%ct%x1f%P%x1f%s%x1e", "--end-of-options", revision,
        )  # fmt: skip
        commits = []
        for record in output.split(b"\x1e"):
            fields = record.strip(b"\n").split(b"\x1f")
            if len(fields) != 4:
                continue
            sha, date, parents, subject = (f.decode("utf-8", "replace") for f in fields)
            commits.append(Commit(sha, int(date), subject, tuple(parents.split())))
        return commits

    def changed_files(self, base: str | None, target: str) -> list[ChangedFile]:
        """Files that change from `base` to `target`; without `base`, those that commit changed."""
        if not _OBJECT_ID.match(target) or (base is not None and not _OBJECT_ID.match(base)):
            raise GitError(GitErrorCode.FAILED)
        revisions = [target] if base is None else [base, target]
        output = self._run(
            "diff-tree", "-r", "-z", "--root", "--no-commit-id", "--name-status", "--no-renames",
            "--no-ext-diff", "--no-textconv", "--end-of-options", *revisions,
        )  # fmt: skip
        fields = output.split(b"\x00")
        changes = []
        for status, raw_path in zip(fields[0::2], fields[1::2], strict=False):
            try:
                changes.append(ChangedFile(status.decode("ascii"), raw_path.decode("utf-8")))
            except UnicodeDecodeError:
                continue
        return changes
