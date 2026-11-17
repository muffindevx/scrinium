"""`scrinium hooks install | uninstall`: `scrinium check` before each commit (phase 2.3).

Optional, and polite with the hooks already there:

- with no pre-commit hook, it writes one (marked as Scrinium's);
- with a shell hook of someone else (husky, pre-commit, a hand-written one), it adds a block
  right after the first line, between two marker comments, so it runs before any `exit`;
- uninstalling removes only that block, and the file only if Scrinium created it and nothing
  else was added since.

It honours `core.hooksPath`. A hook that is not a shell script is never edited: the person is
told the line to add.
"""

import re
import shlex
import stat
from pathlib import Path
from typing import Final

from scrinium.application.check.git import Repository, git

_CREATED: Final = "# Created by `scrinium hooks install`."
_SHELL: Final = re.compile(r"^#!.*\b(?:ba|z|da|k)?sh\b")


class HookError(RuntimeError):
    pass


def _markers(prefix: str) -> tuple[str, str]:
    where = f" {prefix}" if prefix else ""
    return f"# >>> scrinium check{where} >>>", f"# <<< scrinium check{where} <<<"


def hook_path(repository: Repository) -> Path:
    hooks = Path(git(repository.top, "rev-parse", "--git-path", "hooks").strip())
    return (hooks if hooks.is_absolute() else repository.top / hooks) / "pre-commit"


def block(repository: Repository, command: list[str]) -> str:
    begin, end = _markers(repository.prefix)
    run = shlex.join([*command, "check", "--base", "HEAD", "--staged", "--format", "markdown"])
    if repository.prefix:
        run += f" --root {shlex.quote(repository.prefix)}"
    return "\n".join(
        [
            begin,
            "# Stops the commit if it changes a verified rule (`scrinium hooks uninstall` removes",
            "# this block). Skip it once with `git commit --no-verify`.",
            "if git rev-parse -q --verify HEAD >/dev/null; then",
            f"  {run} || exit 1",
            "fi",
            end,
        ]
    )


def install(repository: Repository, command: list[str]) -> str:
    """Returns what was done: "created", "added" or "present"."""
    path = hook_path(repository)
    begin, _ = _markers(repository.prefix)
    text = block(repository, command)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\n{_CREATED}\n{text}\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return "created"
    current = path.read_text(encoding="utf-8")
    if begin in current:
        return "present"
    lines = current.splitlines()
    if lines and lines[0].startswith("#!") and not _SHELL.match(lines[0]):
        raise HookError(
            f"{path} no es un script de shell. Añade tú esta línea donde corresponda: "
            + shlex.join([*command, "check", "--base", "HEAD", "--staged"])
        )
    head = [lines[0]] if lines and lines[0].startswith("#!") else []
    rest = lines[len(head) :]
    path.write_text("\n".join([*head, text, *rest]) + "\n", encoding="utf-8")
    return "added"


def uninstall(repository: Repository) -> str:
    """Returns what was done: "removed", "deleted" (the whole file was ours) or "absent"."""
    path = hook_path(repository)
    begin, end = _markers(repository.prefix)
    if not path.exists():
        return "absent"
    lines = path.read_text(encoding="utf-8").splitlines()
    if begin not in lines:
        return "absent"
    start = lines.index(begin)
    stop = lines.index(end, start) if end in lines[start:] else start
    remaining = lines[:start] + lines[stop + 1 :]
    meaningful = [line for line in remaining if line.strip() and line != _CREATED]
    if _CREATED in remaining and all(line.startswith("#!") for line in meaningful):
        path.unlink()
        return "deleted"
    path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
    return "removed"
