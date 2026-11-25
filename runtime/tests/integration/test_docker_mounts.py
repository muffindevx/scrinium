"""In Docker: the folders given to `docker/scrinium.sh start` are read without another step."""

from pathlib import Path

import pytest
from sqlalchemy import select

from scrinium.application.ingestion import docker_mounts
from scrinium.application.ingestion.docker_mounts import Mount
from scrinium.application.ingestion.plan import SourceError, resolve_root
from scrinium.application.ingestion.policy import SourceKind
from scrinium.infrastructure.sqlite.models import Project, SourceConnection, SyncRun
from tests.conftest import Runtime


def _shop(tmp_path: Path) -> Path:
    folder = tmp_path / "repos" / "shop"
    folder.mkdir(parents=True)
    (folder / "shipping.py").write_text("FREE_FROM = 40\n")
    return folder


def test_the_list_from_the_script_is_read() -> None:
    raw = "/home/ana/code/shop\t/repos/shop,/media/disk/api/\t/repos/api"
    assert docker_mounts.mounts({docker_mounts.ENV: raw}) == [
        Mount("/home/ana/code/shop", "/repos/shop"),
        Mount("/media/disk/api", "/repos/api"),
    ]
    assert docker_mounts.mounts({docker_mounts.ENV: ""}) == []
    assert docker_mounts.mounts({}) is None


def test_a_shared_folder_becomes_a_project_and_is_synced(runtime: Runtime, tmp_path: Path) -> None:
    folder = _shop(tmp_path)
    shared = [Mount("/home/ana/code/shop", str(folder))]
    data_dir = runtime.config.data_dir

    first = docker_mounts.register(runtime.database, runtime.runner.submit, data_dir, shared)
    assert first == [docker_mounts.Added("/home/ana/code/shop", "shop", new=True, git=False)]
    # Started again with the same folder: the same project, read again.
    again = docker_mounts.register(runtime.database, runtime.runner.submit, data_dir, shared)
    assert [added.new for added in again] == [False]

    with runtime.database.session() as session:
        assert session.scalars(select(Project.name)).all() == ["shop"]
        source = session.scalars(select(SourceConnection)).one()
        assert source.configuration["root"] == str(folder)
        assert len(session.scalars(select(SyncRun)).all()) == 2


def test_the_console_takes_the_path_on_the_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = _shop(tmp_path)
    monkeypatch.setenv(docker_mounts.ENV, f"/home/ana/code/shop\t{folder}")
    data_dir = tmp_path / "data"

    assert resolve_root(SourceKind.FOLDER, "/home/ana/code/shop", data_dir) == folder
    with pytest.raises(SourceError) as refused:
        resolve_root(SourceKind.FOLDER, "/media/ana/other-project", data_dir)
    assert refused.value.code == "not_shared_with_docker"
