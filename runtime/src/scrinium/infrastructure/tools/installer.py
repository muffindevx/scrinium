"""Downloads and installs the manifest's tools and models into the data directory.

Each file is checked against its SHA256 before opening, and unpacked without letting an
entry write outside its folder. Installation is atomic: either complete or not there at all.
"""

import ctypes.util
import hashlib
import ipaddress
import os
import platform as platform_module
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from scrinium.infrastructure.tools.manifest import Asset, Model, Tool, Variant

TOOLS_DIRNAME: Final = "tools"
MODELS_DIRNAME: Final = "models"
_CHUNK_BYTES: Final = 1024 * 1024
_TIMEOUT_SECONDS: Final = 30
_ARCHES: Final = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}

Progress = Callable[[int, int], None]


class ToolError(RuntimeError):
    """A tool could not be installed or used. The message is for the end user."""


def current_platform(system: str = sys.platform, machine: str | None = None) -> tuple[str, str]:
    if system.startswith("linux"):
        name = "linux"
    elif system == "darwin":
        name = "macos"
    elif system == "win32":
        name = "windows"
    else:
        raise ToolError(f"Sistema no soportado: {system}")
    arch = _ARCHES.get((machine or platform_module.machine()).lower())
    if arch is None:
        raise ToolError(f"Arquitectura no soportada: {machine or platform_module.machine()}")
    return name, arch


def has_vulkan(system: str = sys.platform) -> bool:
    """Whether a Vulkan loader exists. Without it, the Vulkan build of llama.cpp does not start."""
    return ctypes.util.find_library("vulkan-1" if system == "win32" else "vulkan") is not None


def choose_variant(tool: Tool, os_name: str, arch: str, prefer_cpu: bool = False) -> Variant:
    if (os_name, arch, Variant.DEFAULT) in tool.assets:
        return Variant.DEFAULT
    if not prefer_cpu and has_vulkan() and (os_name, arch, Variant.VULKAN) in tool.assets:
        return Variant.VULKAN
    return Variant.CPU


def _install_dir(tools_dir: Path, tool: Tool, variant: Variant) -> Path:
    return tools_dir / tool.name / f"{tool.version}-{variant}"


def _asset(tool: Tool, os_name: str, arch: str, variant: Variant) -> Asset:
    asset = tool.assets.get((os_name, arch, variant))
    if asset is None:
        raise ToolError(f"{tool.name} no tiene version para {os_name} {arch} ({variant})")
    return asset


def locate(tool: Tool, tools_dir: Path, os_name: str, arch: str) -> Path | None:
    """The installed executable of the manifest's version, of any variant."""
    for (asset_os, asset_arch, variant), asset in tool.assets.items():
        if (asset_os, asset_arch) != (os_name, arch):
            continue
        executable = _install_dir(tools_dir, tool, variant) / asset.executable
        if executable.is_file():
            return executable
    return None


def _check_source(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme == "https":
        return
    # http only towards this machine: tests use it with a local server.
    try:
        loopback = ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if parts.scheme != "http" or not loopback:
        raise ToolError(f"Origen de descarga no permitido: {url}")


def download(url: str, destination: Path, size: int, progress: Progress | None = None) -> str:
    """Downloads `url` and returns its SHA256. Stops if it receives more bytes than expected.

    It honours HTTPS_PROXY: in a company it may be the only way out, and no data
    of the user travels here, only a public file is requested.
    """
    _check_source(url)
    digest = hashlib.sha256()
    received = 0
    try:
        with (
            urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response,  # noqa: S310
            destination.open("wb") as output,
        ):
            while chunk := response.read(_CHUNK_BYTES):
                received += len(chunk)
                if received > size:
                    raise ToolError("La descarga es mas grande de lo esperado: se descarta.")
                digest.update(chunk)
                output.write(chunk)
                if progress is not None:
                    progress(received, size)
    except OSError as error:
        raise ToolError(f"No se pudo descargar {url}: {error}") from None
    return digest.hexdigest()


def _extract(archive: Path, url: str, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    if url.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                target = (destination / member.filename).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise ToolError("El archivo descargado intenta escribir fuera de su carpeta.")
            bundle.extractall(destination)  # noqa: S202  paths checked right above
        return
    with tarfile.open(archive) as bundle:
        try:
            # "data" rejects absolute paths, links leaving the folder, and devices.
            bundle.extractall(destination, filter="data")
        except tarfile.FilterError:
            raise ToolError("El archivo descargado intenta escribir fuera de su carpeta.") from None


def install(
    tool: Tool,
    tools_dir: Path,
    os_name: str,
    arch: str,
    variant: Variant,
    progress: Progress | None = None,
) -> Path:
    """Installs the tool if missing and returns its executable path."""
    asset = _asset(tool, os_name, arch, variant)
    target = _install_dir(tools_dir, tool, variant)
    executable = target / asset.executable
    if executable.is_file():
        return executable

    # Private like the rest of the data directory: mkdir only applies `mode` to the last one.
    for directory in (tools_dir, target.parent):
        directory.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".descarga-") as scratch:
        archive = Path(scratch) / "archivo"
        if download(asset.url, archive, asset.size, progress) != asset.sha256:
            raise ToolError(f"El SHA256 de {tool.name} no coincide con el publicado: se descarta.")
        extracted = Path(scratch) / "contenido"
        _extract(archive, asset.url, extracted)
        installed = extracted / asset.executable
        if not installed.is_file():
            raise ToolError(f"El archivo de {tool.name} no trae {asset.executable}.")
        if os.name == "posix":
            installed.chmod(installed.stat().st_mode | stat.S_IXUSR)
        # A previous half-finished install is useless: it is replaced whole.
        if target.exists():
            shutil.rmtree(target)
        os.replace(extracted, target)
    return executable


def _model_path(model: Model, models_dir: Path) -> Path:
    return models_dir / model.name / model.revision / model.file


def locate_model(model: Model, models_dir: Path) -> Path | None:
    path = _model_path(model, models_dir)
    return path if path.is_file() else None


def install_model(model: Model, models_dir: Path, progress: Progress | None = None) -> Path:
    """Downloads the GGUF if missing and returns its path. Not unpacked: it is a single file."""
    path = _model_path(model, models_dir)
    if path.is_file():
        return path
    for directory in (models_dir, path.parent.parent, path.parent):
        directory.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent, prefix=".descarga-") as scratch:
        partial = Path(scratch) / model.file
        if download(model.url, partial, model.size, progress) != model.sha256:
            raise ToolError(f"El SHA256 de {model.name} no coincide con el publicado: se descarta.")
        partial.chmod(0o600)
        os.replace(partial, path)
    return path
