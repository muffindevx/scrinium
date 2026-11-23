"""The downloads folder (ADR-015): moving llama.cpp and the models, and checking them after."""

import os
import time
from pathlib import Path

import pytest

from scrinium.cli import maintenance
from scrinium.infrastructure.tools import downloads
from scrinium.infrastructure.tools.downloads import IntegrityError, TargetError
from scrinium.infrastructure.tools.manifest import EMBEDDING_MODEL, LLAMA_SERVER
from tests.conftest import ORIGIN, Runtime


def _install(root: Path) -> tuple[Path, Path]:
    """A llama.cpp tree (with a library link, as the real one has) and a model, by hand."""
    tree = root / "tools" / LLAMA_SERVER.name / f"{LLAMA_SERVER.version}-vulkan"
    folder = tree / f"llama-{LLAMA_SERVER.version}"
    folder.mkdir(parents=True)
    (folder / "llama-server").write_bytes(b"#!/bin/sh\necho llama\n")
    (folder / "libllama.so.0").write_bytes(b"biblioteca" * 100)
    (folder / "libllama.so").symlink_to("libllama.so.0")
    model = root / "models" / EMBEDDING_MODEL.name / EMBEDDING_MODEL.revision / EMBEDDING_MODEL.file
    model.parent.mkdir(parents=True)
    model.write_bytes(b"gguf" * 1000)
    return tree, model


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(downloads.DOWNLOADS_ENV, raising=False)
    path = tmp_path / "data"
    path.mkdir(mode=0o700)
    return path


def test_downloads_live_in_the_data_directory_until_moved(data_dir: Path) -> None:
    tree, model = _install(data_dir)
    assert downloads.downloads_dir(data_dir) == data_dir
    assert downloads.is_private(data_dir)
    # Private to the account like the database: nothing to record or check.
    downloads.record(data_dir, tree, model)
    assert not (data_dir / downloads.STATE_FILE).exists()
    downloads.verify(data_dir, tree, full=True)


def test_a_move_copies_checks_switches_and_deletes_the_old_copy_later(
    data_dir: Path, tmp_path: Path
) -> None:
    tree, model = _install(data_dir)
    (data_dir / "tools" / LLAMA_SERVER.name / "b1-vulkan").mkdir()  # an old version: not moved
    (tmp_path / "disco").mkdir()
    target = tmp_path / "disco" / "scrinium"
    seen: list[tuple[int, int]] = []

    downloads.move(data_dir, target, lambda done, total: seen.append((done, total)))

    assert downloads.downloads_dir(data_dir) == target.resolve()
    assert not downloads.is_private(data_dir)
    new_tree = target / tree.relative_to(data_dir)
    new_model = target / model.relative_to(data_dir)
    assert new_model.read_bytes() == model.read_bytes()
    link = new_tree / f"llama-{LLAMA_SERVER.version}" / "libllama.so"
    assert link.is_symlink() and os.readlink(link) == "libllama.so.0"
    assert not (target / "tools" / LLAMA_SERVER.name / "b1-vulkan").exists()
    assert seen and seen[-1][0] == seen[-1][1]
    downloads.verify(data_dir, new_tree, full=True)
    downloads.verify(data_dir, new_model, full=False)

    # The old copy waits: a running llama-server may still load a model from it.
    assert downloads.pending_cleanup(data_dir) == data_dir
    assert model.exists()
    assert downloads.finish_cleanup(data_dir) == data_dir
    assert not (data_dir / "tools").exists() and not (data_dir / "models").exists()
    assert downloads.pending_cleanup(data_dir) is None


def test_outside_the_data_directory_a_changed_file_is_not_used(
    data_dir: Path, tmp_path: Path
) -> None:
    _install(data_dir)
    target = tmp_path / "fuera"
    downloads.move(data_dir, target)
    tree = downloads.tools_dir(data_dir) / LLAMA_SERVER.name / f"{LLAMA_SERVER.version}-vulkan"
    library = tree / f"llama-{LLAMA_SERVER.version}" / "libllama.so.0"
    model = next(downloads.models_dir(data_dir).rglob("*.gguf"))

    # Same bytes, new time (a backup tool touched it): fine, and not hashed again next time.
    later = time.time_ns() + 10**9
    os.utime(model, ns=(later, later))
    downloads.verify(data_dir, model, full=False)

    # Another account rewrote a library, keeping its size: only a full check sees it.
    library.write_bytes(b"X" + library.read_bytes()[1:])
    with pytest.raises(IntegrityError, match="cambió"):
        downloads.verify(data_dir, tree, full=True)
    library.write_bytes(b"b" + library.read_bytes()[1:])
    os.utime(library, ns=(later, later))
    downloads.verify(data_dir, tree, full=True)

    # A library dropped next to llama-server would be loaded by it.
    (library.parent / "libggml-extra.so").write_bytes(b"intrusa")
    with pytest.raises(IntegrityError, match="no lo instaló"):
        downloads.verify(data_dir, tree, full=True)
    (library.parent / "libggml-extra.so").unlink()

    # A link pointed elsewhere.
    link = library.parent / "libllama.so"
    link.unlink()
    link.symlink_to("../otra.so")
    with pytest.raises(IntegrityError, match="enlace"):
        downloads.verify(data_dir, tree, full=False)

    # A model of another size is caught even without a full check.
    with model.open("ab") as output:
        output.write(b"mas")
    with pytest.raises(IntegrityError):
        downloads.verify(data_dir, model, full=False)

    # Something never recorded there is not trusted at all.
    stray = downloads.models_dir(data_dir) / "otro" / "r" / "m.gguf"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"x")
    with pytest.raises(IntegrityError, match="registro"):
        downloads.verify(data_dir, stray, full=True)


def test_folders_it_cannot_move_to_say_why(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(data_dir)

    def code(target: Path) -> str:
        with pytest.raises(TargetError) as caught:
            downloads.check_target(data_dir, target)
        return caught.value.code

    assert code(Path("relativa")) == "not_absolute"
    assert code(data_dir) == "same_dir"
    assert code(data_dir / "dentro") == "inside_data_dir"
    assert code(tmp_path / "no" / "existe") == "no_parent"
    (tmp_path / "archivo").write_text("x")
    assert code(tmp_path / "archivo") == "not_a_dir"
    taken = tmp_path / "ocupada"
    (taken / "models" / "algo").mkdir(parents=True)
    assert code(taken) == "not_empty"
    monkeypatch.setattr(downloads, "_SPARE_BYTES", 10**18)
    assert code(tmp_path / "nueva") == "no_space"
    monkeypatch.setenv(downloads.DOWNLOADS_ENV, str(tmp_path / "env"))
    assert code(tmp_path / "nueva") == "from_env"


def test_a_failed_copy_leaves_everything_as_it_was(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, model = _install(data_dir)
    target = tmp_path / "destino"
    monkeypatch.setattr(downloads, "_copy", lambda *_args: "0" * 64)
    with pytest.raises(IntegrityError, match="no se mueve nada"):
        downloads.move(data_dir, target)
    assert downloads.downloads_dir(data_dir) == data_dir
    assert not (target / "tools").exists() and not (target / "models").exists()
    assert model.exists()


def test_moving_back_to_the_data_directory(data_dir: Path, tmp_path: Path) -> None:
    _install(data_dir)
    away = tmp_path / "fuera"
    downloads.move(data_dir, away)
    downloads.finish_cleanup(data_dir)
    downloads.move(data_dir, data_dir)
    assert downloads.downloads_dir(data_dir) == data_dir and downloads.is_private(data_dir)
    assert next((data_dir / "models").rglob("*.gguf")).is_file()
    assert downloads.pending_cleanup(data_dir) == away.resolve()
    downloads.finish_cleanup(data_dir)
    assert not (away / "models").exists()


def test_clean_and_uninstall_find_the_moved_downloads(data_dir: Path, tmp_path: Path) -> None:
    _install(data_dir)
    away = tmp_path / "fuera"
    downloads.move(data_dir, away)
    reasons = dict(maintenance.leftovers(data_dir))
    assert reasons[data_dir / "models"] == "copia anterior de las descargas, ya movidas"
    owned = maintenance._owned(data_dir)
    assert away.resolve() / "models" in owned and away.resolve() / "tools" in owned
    assert data_dir / downloads.STATE_FILE in owned
    # Nothing else of that folder belongs to Scrinium.
    (away / "mio.txt").write_text("de la persona")
    assert away / "mio.txt" not in maintenance._owned(data_dir)


def test_settings_start_a_move_and_show_its_progress(runtime: Runtime, tmp_path: Path) -> None:
    data_dir = runtime.config.data_dir
    _install(data_dir)
    headers = {"Origin": ORIGIN, "X-CSRF-Token": runtime.login()}
    client = runtime.client
    shown = client.get("/api/v1/system/engine").json()["downloads"]
    assert shown["dir"] == str(data_dir) and shown["private"] and shown["move"] is None

    bad = client.post("/api/v1/system/downloads/move", json={"dir": "relativa"}, headers=headers)
    assert bad.status_code == 409 and bad.json()["error"] == "not_absolute"
    no_csrf = client.post(
        "/api/v1/system/downloads/move",
        json={"dir": str(tmp_path / "x")},
        headers={"Origin": ORIGIN},
    )
    assert no_csrf.status_code == 403

    target = tmp_path / "disco"
    started = client.post(
        "/api/v1/system/downloads/move", json={"dir": str(target)}, headers=headers
    )
    assert started.status_code == 202
    for _ in range(200):
        shown = client.get("/api/v1/system/engine").json()["downloads"]
        if shown["move"]["state"] != "running":
            break
        time.sleep(0.02)
    assert shown["move"]["state"] == "done"
    assert shown["dir"] == str(target.resolve()) and not shown["private"]
    assert shown["pending_cleanup"] == str(data_dir)
    engine = client.get("/api/v1/system/engine").json()
    assert [model["installed"] for model in engine["models"]] == [False, True]
