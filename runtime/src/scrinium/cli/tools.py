"""`scrinium tools`: installs, shows and moves what Scrinium downloads (llama.cpp, models)."""

import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from scrinium.core import instances
from scrinium.infrastructure.tools import downloads
from scrinium.infrastructure.tools.installer import (
    ToolError,
    choose_variant,
    current_platform,
    install,
    install_dir,
    install_model,
    locate,
    locate_model,
    model_path,
)
from scrinium.infrastructure.tools.manifest import (
    ADDONS,
    CHAT_MODEL,
    DEFAULT_INSTALL,
    LOCAL_ADDON,
    MODELS,
    TOOLS,
)


class _Progress:
    """Percentage on a single line; only every 10 %, so the terminal is not flooded."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._shown = -1

    def __call__(self, received: int, total: int) -> None:
        step = received * 10 // max(total, 1)
        if step != self._shown:
            self._shown = step
            print(f"\r  {self._name}: {step * 10} %", end="", flush=True)


def _progress(name: str) -> _Progress | None:
    return _Progress(name) if sys.stdout.isatty() else None


def expand(names: Sequence[str]) -> list[str]:
    """No names: the default install (ADR-013). An add-on (`local`) stands for its parts."""
    wanted: list[str] = []
    for name in names or DEFAULT_INSTALL:
        for part in ADDONS.get(name, (name,)):
            if part not in wanted:
                wanted.append(part)
    return wanted


def _drop_unrecorded(data_dir: Path, path: Path, sha256: str | None) -> None:
    """Outside the data directory, a file installed without a record (Scrinium did not see it
    arrive) is downloaded again; a model whose bytes match the manifest is kept."""
    if not path.exists() or downloads.is_private(data_dir) or downloads.has_record(data_dir, path):
        return
    if sha256 is not None and downloads.matches(path, sha256):
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def install_tools(
    data_dir: Path, names: Sequence[str], prefer_cpu: bool, target: Path | None = None
) -> int:
    current = downloads.downloads_dir(data_dir).resolve()
    moving = target is not None and target.expanduser().resolve() != current
    if moving and move_tools(data_dir, target) != 0:  # type: ignore[arg-type]
        return 1
    tools_dir = downloads.tools_dir(data_dir)
    models_dir = downloads.models_dir(data_dir)
    print(f"Carpeta de descargas: {downloads.downloads_dir(data_dir)}", flush=True)
    try:
        os_name, arch = current_platform()
    except ToolError as error:
        print(error, file=sys.stderr)
        return 1
    wanted = expand(names)
    failed = False
    for name in wanted:
        try:
            if name in TOOLS:
                tool = TOOLS[name]
                variant = choose_variant(tool, os_name, arch, prefer_cpu)
                asset = tool.assets.get((os_name, arch, variant))
                size = f" ({asset.size // 1_000_000} MB)" if asset is not None else ""
                print(f"{tool.name} {tool.version}, {variant}{size}", flush=True)
                tree = install_dir(tools_dir, tool, variant)
                _drop_unrecorded(data_dir, tree, None)
                path = install(tool, tools_dir, os_name, arch, variant, _progress(name))
                downloads.record(data_dir, tree)
            else:
                model = MODELS[name]
                print(f"{model.name}, {model.file} ({model.size // 1_000_000} MB)", flush=True)
                _drop_unrecorded(data_dir, model_path(model, models_dir), model.sha256)
                path = install_model(model, models_dir, _progress(name))
                downloads.record(data_dir, path)
        except ToolError as error:
            print(f"\n  {error}", file=sys.stderr, flush=True)
            failed = True
            continue
        print(f"\r  instalado en {path}", flush=True)
    return 1 if failed else 0


def show_tools(data_dir: Path) -> int:
    tools_dir = downloads.tools_dir(data_dir)
    models_dir = downloads.models_dir(data_dir)
    where = downloads.downloads_dir(data_dir)
    origin = (
        f" (variable {downloads.DOWNLOADS_ENV})"
        if downloads.from_env()
        else " (carpeta de datos)"
        if downloads.is_private(data_dir)
        else ""
    )
    print(f"Carpeta de descargas: {where}{origin}")
    try:
        os_name, arch = current_platform()
    except ToolError as error:
        print(error, file=sys.stderr)
        return 1
    missing = False
    found: list[tuple[str, Path | None]] = [
        (f"{tool.name} {tool.version}", locate(tool, tools_dir, os_name, arch))
        for tool in TOOLS.values()
    ]
    found += [
        (f"{model.name} ({model.file})", locate_model(model, models_dir))
        for model in MODELS.values()
    ]
    for label, path in found:
        optional = label.startswith(CHAT_MODEL.name)
        missing = missing or (path is None and not optional)
        shown = path if path is not None else "no instalado"
        if path is None and optional:
            shown = f"no instalado (opcional: scrinium tools install {LOCAL_ADDON})"
        print(f"{label}: {shown}")
    if missing:
        print("Instala lo que falta con: scrinium tools install")
    return 0


def move_tools(data_dir: Path, target: Path) -> int:
    """Moves what is in use to `target` (ADR-015). The old copy is deleted now if Scrinium is
    not running; otherwise at its next start, since llama-server may still read from it."""
    target = target.expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    progress = _progress("copiando y comprobando")
    print(f"Moviendo las descargas a {target}", flush=True)
    try:
        downloads.move(data_dir, target, progress)
    except ToolError as error:
        print(f"\n{error}", file=sys.stderr)
        return 1
    print(f"\r  listo: las descargas están en {downloads.downloads_dir(data_dir)}", flush=True)
    if instances.running(data_dir):
        print("Scrinium está en marcha: la copia anterior se borra cuando lo reinicies.")
    elif (old := downloads.finish_cleanup(data_dir)) is not None:
        print(f"Copia anterior borrada de {old}.")
    return 0
