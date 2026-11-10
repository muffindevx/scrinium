"""`scrinium stop`, `attach`, `clean` and `uninstall` as a person uses them: real processes."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from scrinium.infrastructure.tools.manifest import CHAT_MODEL, LLAMA_SERVER
from tests.integration.test_start_command import _spawn


def _cli(data_dir: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scrinium.cli.main", *args],
        capture_output=True,
        text=True,
        timeout=60,
        input=stdin,
        env={**os.environ, "SCRINIUM_DATA_DIR": str(data_dir)},
    )


def _read_until(process: subprocess.Popen[str], needle: str, timeout: float = 20) -> list[str]:
    assert process.stdout is not None
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        lines.append(line)
        if needle in line:
            return lines
    raise AssertionError(f"{needle!r} did not appear: {lines}")


def test_attach_shows_activity_live_and_stop_ends_everything(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    instance = _spawn(data_dir, "--no-browser")
    record = json.loads((data_dir / "run" / f"{instance.port}.json").read_text())
    assert record["pid"] == instance.process.pid
    assert oct((data_dir / "run" / f"{instance.port}.json").stat().st_mode)[-3:] == "600"

    attach = subprocess.Popen(
        [sys.executable, "-m", "scrinium.cli.main", "attach"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "SCRINIUM_DATA_DIR": str(data_dir)},
    )
    try:
        _read_until(attach, "Consola enganchada")
        httpx.get(f"http://127.0.0.1:{instance.port}/api/v1/projects")
        seen = "".join(_read_until(attach, "path=/api/v1/projects"))
        assert "reason=session_required" in seen

        stopped = _cli(data_dir, "stop")
        assert stopped.returncode == 0, stopped.stderr
        assert "Detenido" in stopped.stdout
        assert instance.process.wait(timeout=30) == 0
        rest, _ = attach.communicate(timeout=30)
        assert "Scrinium se detuvo." in rest
    finally:
        attach.kill()
        instance.process.kill()
    assert list((data_dir / "run").iterdir()) == []
    assert "No hay ningun Scrinium en marcha" in _cli(data_dir, "stop").stdout
    nothing = _cli(data_dir, "attach")
    assert nothing.returncode == 1 and "scrinium start" in nothing.stderr


def test_control_routes_need_the_key_and_no_browser(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    instance = _spawn(data_dir, "--no-browser")
    try:
        token = json.loads((data_dir / "run" / f"{instance.port}.json").read_text())[
            "control_token"
        ]
        base = f"http://127.0.0.1:{instance.port}"
        assert httpx.post(f"{base}/control/shutdown").status_code == 403
        assert httpx.get(f"{base}/control/logs").status_code == 403
        assert (
            httpx.post(
                f"{base}/control/shutdown", headers={"X-Scrinium-Control": "otra"}
            ).status_code
            == 403
        )
        # With the key but from a browser (Origin): not either.
        from_browser = httpx.post(
            f"{base}/control/shutdown",
            headers={"X-Scrinium-Control": token, "Origin": base},
        )
        assert from_browser.status_code == 403
        assert instance.process.poll() is None
    finally:
        instance.process.terminate()
        instance.process.wait(timeout=30)
    # Shutting down in order deletes its registry entry.
    assert list((data_dir / "run").iterdir()) == []


def _fake_install(data_dir: Path) -> dict[str, Path]:
    tools = data_dir / "tools"
    models = data_dir / "models"
    paths = {
        "current_tool": tools / LLAMA_SERVER.name / f"{LLAMA_SERVER.version}-cpu" / "llama-server",
        "old_tool": tools / LLAMA_SERVER.name / "b1-cpu" / "llama-server",
        "partial_tool": tools / LLAMA_SERVER.name / ".descarga-abc" / "archivo",
        "unknown_tool": tools / "retirada" / "1.0-default" / "x",
        "current_model": models / CHAT_MODEL.name / CHAT_MODEL.revision / CHAT_MODEL.file,
        "partial_model": models / CHAT_MODEL.name / CHAT_MODEL.revision / ".descarga-x" / "m",
        "old_model": models / CHAT_MODEL.name / "revision-vieja" / "viejo.gguf",
        "database": data_dir / "scrinium.sqlite3",
        "stale_run": data_dir / "run" / "1.json",
        "presets": data_dir / "llama" / "presets.ini",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
    paths["stale_run"].write_text(
        json.dumps(
            {"pid": 999999999, "port": 1, "control_token": "t", "version": "0", "started_at": 0}
        )
    )
    return paths


def test_clean_removes_only_leftovers(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    paths = _fake_install(data_dir)

    cancelled = _cli(data_dir, "clean", stdin="n\n")
    assert cancelled.returncode == 1
    assert all(path.exists() for path in paths.values())

    cleaned = _cli(data_dir, "clean", "--si")
    assert cleaned.returncode == 0, cleaned.stderr
    kept = {"current_tool", "current_model", "database"}
    for name, path in paths.items():
        assert path.exists() == (name in kept), name
    assert "No hay nada que limpiar" in _cli(data_dir, "clean", "--si").stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions of the test directory")
def test_uninstall_needs_the_word_and_leaves_foreign_files(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    paths = _fake_install(data_dir)
    foreign = data_dir / "mis-notas.txt"
    foreign.write_text("no es de Scrinium")

    refused = _cli(data_dir, "uninstall", stdin="borrar\n")
    assert refused.returncode == 1 and "no se ha borrado nada" in refused.stdout
    assert all(path.exists() for path in paths.values())

    done = _cli(data_dir, "uninstall", stdin="BORRAR\n")
    assert done.returncode == 0, done.stderr
    assert not any(path.exists() for path in paths.values())
    assert foreign.exists() and "mis-notas.txt" in done.stdout
    # From the source code there is nothing to uninstall with uv.
    assert "codigo fuente" in done.stdout

    foreign.unlink()
    _fake_install(data_dir)
    assert _cli(data_dir, "uninstall", "--si").returncode == 0
    assert not data_dir.exists()
