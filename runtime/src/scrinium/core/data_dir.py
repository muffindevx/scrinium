"""Private data directory: your account's, `0700` or its equivalent ACL on Windows."""

import os
import re
import stat
import sys
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol

_DIR_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_PROBE_MODE: Final = 0o640

# The equivalent of 0700: your account, SYSTEM and Administrators (like root on Linux).
_WINDOWS_SYSTEM: Final = frozenset({"SY", "S-1-5-18"})
_WINDOWS_ADMINS: Final = frozenset({"BA", "S-1-5-32-544"})
_SDDL_OWNER: Final = re.compile(r"O:(.*?)(?=[GDS]:|$)")
_SDDL_DACL: Final = re.compile(r"D:([A-Z_]*)((?:\([^)]*\))*)")
_SDDL_ACE: Final = re.compile(r"\(([^)]*)\)")


class UnsafeDataDirReason(StrEnum):
    NOT_ABSOLUTE = "not_absolute"
    NOT_A_DIRECTORY = "not_a_directory"
    WRONG_OWNER = "wrong_owner"
    PERMISSIONS_IGNORED = "permissions_ignored"


class UnsafeDataDirError(RuntimeError):
    def __init__(self, reason: UnsafeDataDirReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class DataDirPermissions(StrEnum):
    PRIVATE = "private"
    # A system without POSIX permissions that is not Windows either: we cannot check it.
    NOT_CHECKED = "not_checked"


class AclApi(Protocol):
    """Windows ACL in SDDL. The real one is `scrinium.core.windows_acl.WindowsAcl`."""

    def current_user_sid(self) -> str: ...
    def read(self, path: Path) -> str: ...
    def write_dacl(self, path: Path, dacl: str) -> None: ...


def _geteuid() -> int | None:
    geteuid = getattr(os, "geteuid", None)
    return None if geteuid is None else int(geteuid())


def _permissions_work(directory: Path) -> bool:
    """Checks in practice whether `chmod` has an effect: NTFS and exFAT ignore it silently."""
    fd, name = tempfile.mkstemp(prefix=".scrinium-probe-", dir=directory)
    os.close(fd)
    probe = Path(name)
    try:
        os.chmod(probe, _PROBE_MODE)
        first = stat.S_IMODE(probe.stat().st_mode)
        os.chmod(probe, _FILE_MODE)
        second = stat.S_IMODE(probe.stat().st_mode)
        return first == _PROBE_MODE and second == _FILE_MODE
    finally:
        probe.unlink()


def _tighten(path: Path, mode: int, uid: int) -> None:
    info = path.lstat()
    if info.st_uid != uid:
        raise UnsafeDataDirError(UnsafeDataDirReason.WRONG_OWNER)
    if stat.S_IMODE(info.st_mode) & 0o077:
        os.chmod(path, mode)


def _allowed_trustees(user: str) -> frozenset[str]:
    return frozenset({user}) | _WINDOWS_SYSTEM | _WINDOWS_ADMINS


def _is_private_acl(sddl: str, user: str) -> bool:
    """A protected DACL (no inheritance from the parent) that only grants the allowed accounts."""
    dacl = _SDDL_DACL.search(sddl)
    if dacl is None or "P" not in dacl.group(1) or "NO_ACCESS_CONTROL" in dacl.group(1):
        return False
    allowed = _allowed_trustees(user)
    granted = set()
    for ace in _SDDL_ACE.findall(dacl.group(2)):
        # type;flags;rights;object;inherited object;account[;attribute]
        fields = ace.split(";")
        # Deny entries open nothing; any other kind of permission does.
        if fields[0] == "D":
            continue
        trustee = fields[5] if len(fields) > 5 else ""
        if trustee not in allowed:
            return False
        granted.add(trustee)
    return user in granted


def _tighten_windows(path: Path, user: str, dacl: str, acl: AclApi) -> None:
    owner = _SDDL_OWNER.search(acl.read(path))
    # From an elevated console, what is created belongs to Administrators.
    if owner is None or owner.group(1) not in {user} | _WINDOWS_ADMINS:
        raise UnsafeDataDirError(UnsafeDataDirReason.WRONG_OWNER)
    if _is_private_acl(acl.read(path), user):
        return
    try:
        acl.write_dacl(path, dacl)
    except OSError:
        raise UnsafeDataDirError(UnsafeDataDirReason.PERMISSIONS_IGNORED) from None
    # FAT, exFAT and some network drives keep no ACL: check what was left.
    if not _is_private_acl(acl.read(path), user):
        raise UnsafeDataDirError(UnsafeDataDirReason.PERMISSIONS_IGNORED)


def _prepare_windows(real: Path, acl: AclApi) -> DataDirPermissions:
    user = acl.current_user_sid()
    trustees = (user, "SY", "BA")
    _tighten_windows(real, user, "D:P" + "".join(f"(A;OICI;FA;;;{t})" for t in trustees), acl)
    for child in real.iterdir():
        if child.is_symlink() or not child.is_file():
            continue
        _tighten_windows(child, user, "D:P" + "".join(f"(A;;FA;;;{t})" for t in trustees), acl)
    return DataDirPermissions.PRIVATE


def prepare_data_dir(
    path: Path, platform: str = sys.platform, acl: AclApi | None = None
) -> DataDirPermissions:
    """Creates or fixes the data directory. Raises `UnsafeDataDirError` if it cannot be private.

    Fixes overly broad permissions on what is yours; touches nothing belonging to another account.
    """
    if not path.is_absolute():
        raise UnsafeDataDirError(UnsafeDataDirReason.NOT_ABSOLUTE)
    if path.exists() and not path.is_dir():
        raise UnsafeDataDirError(UnsafeDataDirReason.NOT_A_DIRECTORY)
    path.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    # A symbolic link is followed: what is protected is the real folder.
    real = path.resolve()

    if platform == "win32":
        if acl is None:
            from scrinium.core.windows_acl import WindowsAcl

            acl = WindowsAcl()
        return _prepare_windows(real, acl)

    uid = _geteuid()
    if uid is None:
        return DataDirPermissions.NOT_CHECKED

    _tighten(real, _DIR_MODE, uid)
    if not _permissions_work(real) or stat.S_IMODE(real.stat().st_mode) != _DIR_MODE:
        raise UnsafeDataDirError(UnsafeDataDirReason.PERMISSIONS_IGNORED)
    for child in real.iterdir():
        if child.is_symlink() or not child.is_file():
            continue
        _tighten(child, _FILE_MODE, uid)
    return DataDirPermissions.PRIVATE
