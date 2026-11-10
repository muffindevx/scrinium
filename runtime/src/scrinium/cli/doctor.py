"""`scrinium doctor`: checks the machine and says how to fix what is missing.

It only reads: it downloads, installs and changes nothing, except creating the private data
directory if it does not exist yet (as at startup). Exits with 1 if something blocks Scrinium.
"""

import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from scrinium import __version__
from scrinium.cli.window import window_available
from scrinium.core.config import ModelProvider, default_model_provider, default_ollama_url
from scrinium.core.data_dir import DataDirPermissions, UnsafeDataDirError, prepare_data_dir
from scrinium.core.disk_encryption import DiskEncryption, check_disk_encryption
from scrinium.core.memory import total_memory_bytes
from scrinium.infrastructure.local_http import LocalHttpClient, ModelError
from scrinium.infrastructure.secret_store import KeyringSecretStore
from scrinium.infrastructure.tools.installer import (
    MODELS_DIRNAME,
    TOOLS_DIRNAME,
    ToolError,
    current_platform,
    has_vulkan,
    locate,
    locate_model,
)
from scrinium.infrastructure.tools.manifest import LLAMA_SERVER, MODELS, TOOLS

_GB: Final = 1024**3
# Chat (2.5 GB) and embeddings (0.6 GB) loaded, with their context, plus Scrinium and the system.
_MIN_MEMORY_GB: Final = 8
_RUN_TIMEOUT_SECONDS: Final = 20
_INSTALL_FIX: Final = "Instalalo con: scrinium tools install"


class Level(StrEnum):
    OK = "ok"
    WARN = "aviso"
    FAIL = "falla"


@dataclass(frozen=True)
class Check:
    level: Level
    message: str
    fix: str | None = None


def _data_dir(data_dir: Path) -> Check:
    try:
        permissions = prepare_data_dir(data_dir)
    except UnsafeDataDirError as error:
        from scrinium.cli.main import unsafe_data_dir_message

        return Check(Level.FAIL, unsafe_data_dir_message(error.reason).format(path=data_dir))
    if permissions == DataDirPermissions.NOT_CHECKED:
        return Check(Level.WARN, f"Directorio de datos sin comprobar sus permisos: {data_dir}")
    return Check(Level.OK, f"Directorio de datos privado: {data_dir}")


def _disk_encryption(data_dir: Path) -> Check:
    state = check_disk_encryption(data_dir)
    if state == DiskEncryption.ENCRYPTED:
        return Check(Level.OK, "Disco cifrado")
    return Check(
        Level.WARN,
        "No se detecto cifrado de disco: si pierdes el equipo, tus datos se podrian leer.",
        "Activa BitLocker, FileVault o LUKS, segun tu sistema.",
    )


def _keyring() -> Check:
    name = KeyringSecretStore.from_system().backend_name
    if name is not None:
        return Check(Level.OK, f"Almacen de secretos del sistema: {name}")
    fix = "En Linux, instala GNOME Keyring o KWallet y abre sesion en el escritorio."
    return Check(
        Level.WARN, "Sin almacen de secretos: Scrinium no podra guardar claves de acceso.", fix
    )


def _memory() -> Check:
    total = total_memory_bytes()
    if total is None:
        return Check(Level.WARN, "No se pudo saber cuanta memoria tiene el equipo.")
    gigabytes = round(total / _GB)
    if gigabytes < _MIN_MEMORY_GB:
        return Check(
            Level.WARN,
            f"Memoria: {gigabytes} GB. Con menos de {_MIN_MEMORY_GB} GB, las respuestas "
            "seran lentas o el equipo ira justo.",
            "Cierra otros programas pesados (navegadores, maquinas virtuales) mientras usas "
            "Scrinium.",
        )
    return Check(Level.OK, f"Memoria: {gigabytes} GB")


def _disk_space(data_dir: Path, models_dir: Path) -> Check:
    missing = sum(m.size for m in MODELS.values() if locate_model(m, models_dir) is None)
    free = shutil.disk_usage(data_dir).free
    if free < missing + _GB:
        return Check(
            Level.FAIL,
            f"Espacio libre: {free // _GB} GB. Los modelos que faltan necesitan "
            f"{missing // _GB + 1} GB.",
            "Libera espacio o elige otra carpeta con SCRINIUM_DATA_DIR.",
        )
    return Check(Level.OK, f"Espacio libre: {free // _GB} GB")


def _tools(data_dir: Path, os_name: str, arch: str) -> list[Check]:
    checks = []
    for tool in TOOLS.values():
        path = locate(tool, data_dir / TOOLS_DIRNAME, os_name, arch)
        if path is None:
            checks.append(Check(Level.FAIL, f"Falta {tool.name} {tool.version}.", _INSTALL_FIX))
        else:
            checks.append(Check(Level.OK, f"{tool.name} {tool.version}"))
    for model in MODELS.values():
        if locate_model(model, data_dir / MODELS_DIRNAME) is None:
            checks.append(Check(Level.FAIL, f"Falta el modelo {model.name}.", _INSTALL_FIX))
        else:
            checks.append(Check(Level.OK, f"Modelo {model.name}"))
    return checks


def _llama_runs(data_dir: Path, os_name: str, arch: str) -> Check | None:
    """Runs `llama-server --version`: fails if libraries are missing or the CPU is unsuitable."""
    executable = locate(LLAMA_SERVER, data_dir / TOOLS_DIRNAME, os_name, arch)
    if executable is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603  installed, verified executable
            [executable, "--version"],
            capture_output=True,
            timeout=_RUN_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode != 0:
        return Check(
            Level.FAIL,
            "llama-server no se puede ejecutar en este equipo.",
            "Prueba la version solo CPU: scrinium tools install --cpu",
        )
    gpu = "Metal" if os_name == "macos" else "Vulkan" if "vulkan" in str(executable) else "CPU"
    return Check(Level.OK, f"llama-server se ejecuta ({gpu})")


def _gpu(os_name: str) -> Check:
    if os_name == "macos" or has_vulkan():
        return Check(Level.OK, "GPU disponible para los modelos")
    return Check(
        Level.WARN,
        "Sin Vulkan: los modelos usaran solo la CPU y responderan mas despacio.",
        "Instala el controlador de tu tarjeta grafica con soporte Vulkan.",
    )


def _ollama() -> Check:
    try:
        LocalHttpClient(default_ollama_url()).request("/api/tags", None, 3)
    except (ModelError, ValueError):
        return Check(
            Level.FAIL,
            f"SCRINIUM_MODEL_PROVIDER=ollama, pero Ollama no responde en {default_ollama_url()}.",
            "Arranca Ollama o quita SCRINIUM_MODEL_PROVIDER para usar llama.cpp.",
        )
    return Check(Level.OK, f"Ollama responde en {default_ollama_url()}")


def _window() -> Check:
    if window_available():
        return Check(Level.OK, "Ventana propia de Scrinium")
    if sys.platform.startswith("linux") and (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        fix = "Reinstala Scrinium con el extra window (el instalador lo hace si hay escritorio)."
        return Check(Level.WARN, "Sin ventana propia: Scrinium abrira el navegador.", fix)
    return Check(Level.OK, "Sin escritorio: Scrinium usara el navegador")


def run_checks(data_dir: Path) -> list[Check]:
    data = _data_dir(data_dir)
    checks = [data]
    if data.level == Level.FAIL:
        return checks
    try:
        os_name, arch = current_platform()
    except ToolError as error:
        return [*checks, Check(Level.FAIL, str(error))]
    steps: list[Callable[[], Check | list[Check] | None]] = [
        lambda: _disk_encryption(data_dir),
        _keyring,
        _memory,
        lambda: _disk_space(data_dir, data_dir / MODELS_DIRNAME),
        lambda: _tools(data_dir, os_name, arch),
    ]
    try:
        provider = default_model_provider()
    except ValueError:
        steps.append(lambda: Check(Level.FAIL, "SCRINIUM_MODEL_PROVIDER debe ser llama u ollama."))
    else:
        if provider == ModelProvider.OLLAMA:
            steps.append(_ollama)
        else:
            steps += [lambda: _llama_runs(data_dir, os_name, arch), lambda: _gpu(os_name)]
    steps.append(_window)
    for step in steps:
        result = step()
        if isinstance(result, list):
            checks += result
        elif result is not None:
            checks.append(result)
    return checks


def doctor(data_dir: Path) -> int:
    system = f"{platform.system()} {platform.machine()}"
    print(f"Scrinium {__version__}, {system}, Python {platform.python_version()}", flush=True)
    checks = run_checks(data_dir)
    for check in checks:
        print(f"[{check.level}] {check.message}", flush=True)
        if check.fix and check.level != Level.OK:
            print(f"        {check.fix}", flush=True)
    failed = any(check.level == Level.FAIL for check in checks)
    print("Hay que arreglar lo marcado como falla." if failed else "Listo para usar Scrinium.")
    return 1 if failed else 0
