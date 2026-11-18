"""Optional clone by URL (phase 9.6): for a demo, or for people without the code on their machine.

Only `https://` to a named host, with no user or password in the URL (the URL is stored, so it
must never carry a credential). Private repositories authenticate with the system's Git
credential helper, which Scrinium neither reads nor stores.

Git runs hardened, whatever the repository or the person's configuration says:

- a shallow clone of the default branch (`--depth 1 --single-branch --no-tags`) and no checkout:
  Scrinium reads blobs from the object store, so no file of the repository is written to disk
  and no filter, hook or `.gitattributes` program runs;
- `core.hooksPath=/dev/null`, `core.fsmonitor=false`, `protocol.file.allow=never`,
  `protocol.ext.allow=never`, `submodule.recurse=false`, and no prompt on the terminal.

The clone lives in `clones/<id>` inside the data directory: deleting Scrinium's data deletes it.
Syncing such a source fetches the branch's latest commit first; that is the only network access
of a sync, and only for sources the person added by URL.
"""

import ipaddress
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

CLONES_DIRNAME: Final = "clones"
_TIMEOUT_SECONDS: Final = 600
_HARDENED: Final = (
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "protocol.file.allow=never",
    "-c", "protocol.ext.allow=never",
    "-c", "submodule.recurse=false",
    "-c", "credential.interactive=false",
)  # fmt: skip


class CloneError(ValueError):
    """`code` is stable: invalid_url, clone_failed, fetch_failed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def check_url(url: str) -> str:
    """The URL as it will be stored; raises `CloneError("invalid_url")` otherwise."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        raise CloneError("invalid_url")
    if parts.query or parts.fragment or "." not in host or not parts.path.strip("/"):
        raise CloneError("invalid_url")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise CloneError("invalid_url")  # a named service, as for model providers
    return f"https://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def _environment() -> dict[str, str]:
    """The person's environment for credentials (HOME, the credential helper's socket), with
    the terminal prompt and any askpass program turned off."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": ""})
    return env


def _git(cwd: Path, *args: str, error: str) -> None:
    try:
        result = subprocess.run(  # noqa: S603  fixed arguments, no shell
            ["git", *_HARDENED, *args],  # noqa: S607  git from PATH, as everywhere
            cwd=cwd, env=_environment(), stdin=subprocess.DEVNULL, capture_output=True,
            timeout=_TIMEOUT_SECONDS, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        raise CloneError(error) from None
    if result.returncode != 0:
        raise CloneError(error)


def clone(url: str, data_dir: Path) -> Path:
    """Clones `url` into a new folder under `clones/`; returns it."""
    clean = check_url(url)
    clones = data_dir / CLONES_DIRNAME
    clones.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = clones / uuid.uuid4().hex
    try:
        _git(clones, "clone", "--depth", "1", "--single-branch", "--no-tags", "--no-checkout",
             "--", clean, target.name, error="clone_failed")  # fmt: skip
    except CloneError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def refresh(root: Path) -> None:
    """The branch's latest commit, before a sync (shallow, no tags, the branch moved in place)."""
    head = root / ".git" / "HEAD"
    reference = head.read_text(encoding="utf-8").strip() if head.is_file() else ""
    if not reference.startswith("ref: refs/heads/"):
        raise CloneError("fetch_failed")
    branch = reference.removeprefix("ref: refs/heads/")
    _git(root, "fetch", "--depth", "1", "--no-tags", "--update-head-ok", "origin",
         f"+refs/heads/{branch}:refs/heads/{branch}", error="fetch_failed")  # fmt: skip


def is_clone(root: Path, data_dir: Path) -> bool:
    return root.resolve().is_relative_to((data_dir / CLONES_DIRNAME).resolve())
