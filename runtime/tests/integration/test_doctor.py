"""`scrinium doctor` as a person runs it: a separate process and its data directory."""

import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scrinium.infrastructure.tools.installer import MODELS_DIRNAME, TOOLS_DIRNAME, current_platform
from scrinium.infrastructure.tools.manifest import LLAMA_SERVER, MODELS, TOOLS


def _doctor(data_dir: Path, **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k != "SCRINIUM_MODEL_PROVIDER"}
    return subprocess.run(
        [sys.executable, "-m", "scrinium.cli.main", "doctor"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**base, "SCRINIUM_DATA_DIR": str(data_dir), **env},
    )


def _install_fakes(data_dir: Path, llama_exit: int = 0) -> None:
    """Puts each tool and model where `scrinium tools install` would."""
    os_name, arch = current_platform()
    for tool in TOOLS.values():
        variant, asset = next(
            (variant, asset)
            for (asset_os, asset_arch, variant), asset in tool.assets.items()
            if (asset_os, asset_arch) == (os_name, arch)
        )
        executable = data_dir / TOOLS_DIRNAME / tool.name / f"{tool.version}-{variant}"
        executable = executable / asset.executable
        executable.parent.mkdir(parents=True)
        executable.write_text(f"#!/bin/sh\nexit {llama_exit if tool is LLAMA_SERVER else 0}\n")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    for model in MODELS.values():
        path = data_dir / MODELS_DIRNAME / model.name / model.revision / model.file
        path.parent.mkdir(parents=True)
        path.write_bytes(b"GGUF")


def test_an_empty_install_says_what_to_run(tmp_path: Path) -> None:
    result = _doctor(tmp_path / "data")
    assert result.returncode == 1
    assert "[ok] Directorio de datos privado" in result.stdout
    assert "[falla] Falta llama-server" in result.stdout
    assert "[falla] Falta el modelo qwen3-embedding-0.6b." in result.stdout
    # ADR-013: the local chat model is an add-on, never a failure.
    assert "[falla] Falta el modelo qwen3-4b-instruct-2507." not in result.stdout
    assert "scrinium tools install local" in result.stdout
    assert "Primera ejecucion: la consola pregunta" in result.stdout
    assert "scrinium tools install" in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="fake shell executables")
def test_a_complete_install_is_ready(tmp_path: Path) -> None:
    _install_fakes(tmp_path / "data")
    result = _doctor(tmp_path / "data")
    assert result.returncode == 0, result.stdout
    assert "[falla]" not in result.stdout
    assert "llama-server se ejecuta" in result.stdout
    assert result.stdout.rstrip().endswith("Listo para usar Scrinium.")


@pytest.mark.skipif(sys.platform == "win32", reason="fake shell executables")
def test_a_llama_server_that_cannot_run_suggests_the_cpu_build(tmp_path: Path) -> None:
    _install_fakes(tmp_path / "data", llama_exit=127)
    result = _doctor(tmp_path / "data")
    assert result.returncode == 1
    assert "[falla] llama-server no se puede ejecutar" in result.stdout
    assert "scrinium tools install --cpu" in result.stdout


def test_ollama_that_does_not_answer_is_a_failure(tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    result = _doctor(
        tmp_path / "data",
        SCRINIUM_MODEL_PROVIDER="ollama",
        SCRINIUM_OLLAMA_URL=f"http://127.0.0.1:{port}",
    )
    assert result.returncode == 1
    assert "Ollama no responde" in result.stdout
    assert "llama-server se ejecuta" not in result.stdout


def test_an_unsafe_data_dir_stops_the_checks(tmp_path: Path) -> None:
    result = _doctor(Path("relativa"))
    assert result.returncode == 1
    assert "ruta absoluta" in result.stdout
    assert "Memoria" not in result.stdout
