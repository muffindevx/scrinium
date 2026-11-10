"""Detection of system disk encryption. It only reports: Scrinium encrypts nothing itself."""

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Final

_TIMEOUT_SECONDS: Final = 5


class DiskEncryption(StrEnum):
    ENCRYPTED = "encrypted"
    # No full-disk encryption found. There may be another kind (fscrypt, ZFS).
    NOT_DETECTED = "not_detected"
    UNKNOWN = "unknown"


Runner = Callable[[Sequence[str]], str | None]


def _run(command: Sequence[str]) -> str | None:
    """Runs a system tool without a shell. Returns stdout, or None if it fails."""
    executable = shutil.which(command[0])
    if executable is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603  fixed arguments, no shell
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout


def _linux(path: Path, run: Runner) -> DiskEncryption:
    output = run(["findmnt", "--noheadings", "--output", "SOURCE", "--target", str(path)])
    # btrfs adds the subvolume: /dev/sda2[/@home].
    source = (output or "").strip().split("[")[0]
    # tmpfs, overlay, ZFS or ecryptfs are not a block device: we cannot tell.
    if not source.startswith("/dev/"):
        return DiskEncryption.UNKNOWN
    # --inverse walks from the device to its parents: partition, LUKS, disk...
    types = run(["lsblk", "--inverse", "--noheadings", "--output", "TYPE", source])
    if types is None or not types.strip():
        return DiskEncryption.UNKNOWN
    if "crypt" in types.split():
        return DiskEncryption.ENCRYPTED
    return DiskEncryption.NOT_DETECTED


def _macos(run: Runner) -> DiskEncryption:
    status = (run(["fdesetup", "isactive"]) or "").strip()
    if status == "true":
        return DiskEncryption.ENCRYPTED
    if status == "false":
        return DiskEncryption.NOT_DETECTED
    return DiskEncryption.UNKNOWN


def check_disk_encryption(
    path: Path, platform: str = sys.platform, run: Runner = _run
) -> DiskEncryption:
    """Checks the disk holding `path`. On Windows it needs administrator rights: UNKNOWN."""
    if platform.startswith("linux"):
        return _linux(path, run)
    if platform == "darwin":
        return _macos(run)
    return DiskEncryption.UNKNOWN
