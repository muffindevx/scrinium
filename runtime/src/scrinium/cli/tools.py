"""`scrinium tools`: installs and shows what Scrinium downloads (gitleaks, llama.cpp, models)."""

import sys
from collections.abc import Sequence
from pathlib import Path

from scrinium.infrastructure.tools.installer import (
    MODELS_DIRNAME,
    TOOLS_DIRNAME,
    ToolError,
    choose_variant,
    current_platform,
    install,
    install_model,
    locate,
    locate_model,
)
from scrinium.infrastructure.tools.manifest import MODELS, TOOLS


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


def install_tools(data_dir: Path, names: Sequence[str], prefer_cpu: bool) -> int:
    tools_dir = data_dir / TOOLS_DIRNAME
    try:
        os_name, arch = current_platform()
    except ToolError as error:
        print(error, file=sys.stderr)
        return 1
    wanted = names or [*TOOLS, *MODELS]
    failed = False
    for name in wanted:
        try:
            if name in TOOLS:
                tool = TOOLS[name]
                variant = choose_variant(tool, os_name, arch, prefer_cpu)
                asset = tool.assets.get((os_name, arch, variant))
                size = f" ({asset.size // 1_000_000} MB)" if asset is not None else ""
                print(f"{tool.name} {tool.version}, {variant}{size}", flush=True)
                path = install(tool, tools_dir, os_name, arch, variant, _progress(name))
            else:
                model = MODELS[name]
                print(f"{model.name}, {model.file} ({model.size // 1_000_000} MB)", flush=True)
                path = install_model(model, data_dir / MODELS_DIRNAME, _progress(name))
        except ToolError as error:
            print(f"\n  {error}", file=sys.stderr, flush=True)
            failed = True
            continue
        print(f"\r  instalado en {path}", flush=True)
    return 1 if failed else 0


def show_tools(data_dir: Path) -> int:
    tools_dir = data_dir / TOOLS_DIRNAME
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
        (f"{model.name} ({model.file})", locate_model(model, data_dir / MODELS_DIRNAME))
        for model in MODELS.values()
    ]
    for label, path in found:
        missing = missing or path is None
        print(f"{label}: {path if path is not None else 'no instalado'}")
    if missing:
        print("Instala lo que falta con: scrinium tools install")
    return 0
