"""Data directory: private, your account's, and on a disk that honours permissions."""

import os
import stat
from pathlib import Path

import pytest

from scrinium.core import data_dir
from scrinium.core.data_dir import (
    DataDirPermissions,
    UnsafeDataDirError,
    UnsafeDataDirReason,
    prepare_data_dir,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _reason(path: Path) -> UnsafeDataDirReason:
    with pytest.raises(UnsafeDataDirError) as raised:
        prepare_data_dir(path)
    return raised.value.reason


def test_creates_a_private_directory(tmp_path: Path) -> None:
    target = tmp_path / "a" / "data"
    assert prepare_data_dir(target) == DataDirPermissions.PRIVATE
    assert _mode(target) == 0o700
    assert list(target.iterdir()) == []  # the probe leaves no trace


def test_tightens_loose_permissions_on_own_files(tmp_path: Path) -> None:
    target = tmp_path / "data"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    database = target / "scrinium.sqlite3"
    database.write_text("")
    database.chmod(0o644)
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("")
    elsewhere.chmod(0o644)
    (target / "link").symlink_to(elsewhere)

    prepare_data_dir(target)

    assert _mode(target) == 0o700
    assert _mode(database) == 0o600
    assert _mode(elsewhere) == 0o644  # links are not followed


def test_symlinked_data_dir_protects_the_real_folder(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o755)
    real.chmod(0o755)
    link = tmp_path / "link"
    link.symlink_to(real)
    prepare_data_dir(link)
    assert _mode(real) == 0o700


def test_relative_path_is_refused() -> None:
    assert _reason(Path("datos")) == UnsafeDataDirReason.NOT_ABSOLUTE


def test_a_file_is_not_a_data_dir(tmp_path: Path) -> None:
    target = tmp_path / "data"
    target.write_text("")
    assert _reason(target) == UnsafeDataDirReason.NOT_A_DIRECTORY


def test_folder_of_another_account_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(data_dir, "_geteuid", lambda: os.geteuid() + 1)
    target = tmp_path / "data"
    target.mkdir(mode=0o700)
    assert _reason(target) == UnsafeDataDirReason.WRONG_OWNER
    assert _mode(target) == 0o700


def test_disk_that_ignores_chmod_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This is how NTFS mounted with ntfs-3g behaves: chmod does not fail, but changes nothing.
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: None)
    target = tmp_path / "data"
    assert _reason(target) == UnsafeDataDirReason.PERMISSIONS_IGNORED
    assert list(target.iterdir()) == []


def test_without_posix_permissions_nothing_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(data_dir, "_geteuid", lambda: None)
    assert prepare_data_dir(tmp_path / "data") == DataDirPermissions.NOT_CHECKED


@pytest.mark.skipif(
    not os.environ.get("SCRINIUM_TEST_NTFS_DIR"),
    reason="needs SCRINIUM_TEST_NTFS_DIR, a folder on an NTFS or exFAT disk",
)
def test_real_ntfs_disk_is_refused() -> None:
    target = Path(os.environ["SCRINIUM_TEST_NTFS_DIR"]) / "scrinium-data-test"
    try:
        assert _reason(target) == UnsafeDataDirReason.PERMISSIONS_IGNORED
    finally:
        target.rmdir()


USER = "S-1-5-21-1-2-3-1001"
OTHER = "S-1-5-21-1-2-3-1002"
# What a new folder inherits on another Windows disk: Authenticated Users get in.
INHERITED = f"D:AI(A;OICIID;FA;;;{USER})(A;OICIID;FA;;;SY)(A;OICIID;0x1301bf;;;AU)"


class FakeAcl:
    """In-memory ACL. `keeps_acl=False` behaves like FAT or exFAT: it keeps nothing."""

    def __init__(self, owner: str = USER, dacl: str = INHERITED, keeps_acl: bool = True) -> None:
        self.owner = owner
        self.dacls: dict[Path, str] = {}
        self.default = dacl if keeps_acl else "D:NO_ACCESS_CONTROL"
        self.keeps_acl = keeps_acl
        self.writes: list[Path] = []

    def current_user_sid(self) -> str:
        return USER

    def read(self, path: Path) -> str:
        return f"O:{self.owner}" + self.dacls.get(path, self.default)

    def write_dacl(self, path: Path, dacl: str) -> None:
        self.writes.append(path)
        if self.keeps_acl:
            self.dacls[path] = dacl


def _windows_reason(path: Path, acl: FakeAcl) -> UnsafeDataDirReason:
    with pytest.raises(UnsafeDataDirError) as raised:
        prepare_data_dir(path, platform="win32", acl=acl)
    return raised.value.reason


def test_windows_restricts_the_acl_to_your_account(tmp_path: Path) -> None:
    target = tmp_path / "data"
    target.mkdir()
    (target / "scrinium.sqlite3").write_text("")
    acl = FakeAcl()

    assert prepare_data_dir(target, platform="win32", acl=acl) == DataDirPermissions.PRIVATE

    folder = acl.dacls[target.resolve()]
    assert folder.startswith("D:P")
    assert "AU" not in folder
    assert f"(A;OICI;FA;;;{USER})" in folder
    assert "AU" not in acl.dacls[target.resolve() / "scrinium.sqlite3"]


def test_windows_leaves_an_already_private_acl_alone(tmp_path: Path) -> None:
    acl = FakeAcl(dacl=f"D:PAI(A;OICI;FA;;;{USER})(A;OICI;FA;;;SY)(D;;FA;;;WD)")
    prepare_data_dir(tmp_path / "data", platform="win32", acl=acl)
    assert acl.writes == []


def test_windows_refuses_a_disk_without_acl(tmp_path: Path) -> None:
    assert (
        _windows_reason(tmp_path / "data", FakeAcl(keeps_acl=False))
        == UnsafeDataDirReason.PERMISSIONS_IGNORED
    )


def test_windows_refuses_a_folder_of_another_account(tmp_path: Path) -> None:
    acl = FakeAcl(owner=OTHER)
    assert _windows_reason(tmp_path / "data", acl) == UnsafeDataDirReason.WRONG_OWNER
    assert acl.writes == []


def test_windows_accepts_a_folder_created_from_an_elevated_console(tmp_path: Path) -> None:
    acl = FakeAcl(owner="BA")
    assert prepare_data_dir(tmp_path / "data", platform="win32", acl=acl) == (
        DataDirPermissions.PRIVATE
    )


def test_windows_refuses_when_the_acl_cannot_be_written(tmp_path: Path) -> None:
    class Denied(FakeAcl):
        def write_dacl(self, path: Path, dacl: str) -> None:
            raise PermissionError(5, "Acceso denegado")

    assert _windows_reason(tmp_path / "data", Denied()) == UnsafeDataDirReason.PERMISSIONS_IGNORED
