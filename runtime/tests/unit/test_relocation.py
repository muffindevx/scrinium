from pathlib import Path

from scrinium.application.check.git import git
from scrinium.application.ingestion.policy import SourceKind
from scrinium.application.ingestion.relocation import candidates


def _repo(root: Path, text: str) -> str:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    (root / "a.py").write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", text)
    return git(root, "rev-parse", "HEAD").strip()


def test_a_disk_mounted_under_a_new_name_is_found(tmp_path: Path) -> None:
    old = tmp_path / "media" / "DISK" / "work" / "app"
    commit = _repo(old, "uno")
    _repo(tmp_path / "media" / "OTHER" / "work" / "app", "otro")  # a different repository
    (tmp_path / "media" / "DISK").rename(tmp_path / "media" / "DISK1")
    found = candidates(SourceKind.GIT_REPOSITORY, old, commit)
    assert found == [tmp_path / "media" / "DISK1" / "work" / "app"]


def test_nothing_is_searched_while_the_folder_is_there(tmp_path: Path) -> None:
    root = tmp_path / "app"
    commit = _repo(root, "uno")
    assert candidates(SourceKind.GIT_REPOSITORY, root, commit) == []
