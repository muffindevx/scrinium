"""Disk encryption detection with simulated system tools."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from scrinium.core.disk_encryption import DiskEncryption, Runner, check_disk_encryption

DATA = Path("/home/persona/.local/share/scrinium")


def _runner(outputs: dict[str, str | None]) -> tuple[list[list[str]], Runner]:
    calls: list[list[str]] = []

    def run(command: Sequence[str]) -> str | None:
        calls.append(list(command))
        return outputs.get(command[0])

    return calls, run


@pytest.mark.parametrize(
    ("findmnt", "lsblk", "expected"),
    [
        ("/dev/mapper/vg-root\n", "lvm\ncrypt\npart\ndisk\n", DiskEncryption.ENCRYPTED),
        ("/dev/mapper/luks-1\n", "crypt\npart\ndisk\n", DiskEncryption.ENCRYPTED),
        ("/dev/sda6\n", "part\ndisk\n", DiskEncryption.NOT_DETECTED),
        ("/dev/sda2[/@home]\n", "part\ndisk\n", DiskEncryption.NOT_DETECTED),
        ("tmpfs\n", "", DiskEncryption.UNKNOWN),
        ("/home/persona/.Private\n", "", DiskEncryption.UNKNOWN),
        (None, None, DiskEncryption.UNKNOWN),
        ("/dev/sda6\n", "", DiskEncryption.UNKNOWN),
        ("/dev/sda6\n", None, DiskEncryption.UNKNOWN),
    ],
)
def test_linux(findmnt: str | None, lsblk: str | None, expected: DiskEncryption) -> None:
    calls, run = _runner({"findmnt": findmnt, "lsblk": lsblk})
    assert check_disk_encryption(DATA, "linux", run) == expected
    # The path travels as a separate argument, never inside a shell command.
    assert str(DATA) in calls[0]


def test_lsblk_receives_the_device_without_the_btrfs_subvolume() -> None:
    calls, run = _runner({"findmnt": "/dev/sda2[/@home]\n", "lsblk": "part\n"})
    check_disk_encryption(DATA, "linux", run)
    assert calls[1][-1] == "/dev/sda2"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("true\n", DiskEncryption.ENCRYPTED),
        ("false\n", DiskEncryption.NOT_DETECTED),
        ("", DiskEncryption.UNKNOWN),
        (None, DiskEncryption.UNKNOWN),
    ],
)
def test_macos(output: str | None, expected: DiskEncryption) -> None:
    _, run = _runner({"fdesetup": output})
    assert check_disk_encryption(DATA, "darwin", run) == expected


def test_windows_is_unknown_without_admin_rights() -> None:
    calls, run = _runner({})
    assert check_disk_encryption(DATA, "win32", run) == DiskEncryption.UNKNOWN
    assert calls == []


def test_real_tools_on_this_machine_do_not_fail(tmp_path: Path) -> None:
    assert check_disk_encryption(tmp_path) in set(DiskEncryption)
