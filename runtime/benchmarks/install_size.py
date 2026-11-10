"""Installed size of Scrinium by direct dependency (PERFORMANCE.md, section 3).

Counts each direct dependency with its own dependencies, without development tools. The
models, llama-server and gitleaks do not count: they are downloaded separately (`scrinium tools`).

Usage: make size
"""

import re
from importlib import metadata
from pathlib import Path

# The `window` extra on Linux: pywebview with Qt and its bundled browser.
WINDOW_PACKAGES = (
    "pywebview", "qtpy", "pyqt6", "pyqt6-qt6", "pyqt6-sip", "pyqt6-webengine",
    "pyqt6-webengine-qt6",
)  # fmt: skip


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


DISTS = {_norm(d.metadata["Name"]): d for d in metadata.distributions()}


def _size(name: str) -> int:
    dist = DISTS[name]
    files = (Path(str(dist.locate_file(f))) for f in dist.files or [])
    return sum(path.stat().st_size for path in files if path.is_file())


def _requires(name: str) -> list[str]:
    names = []
    for requirement in DISTS[name].requires or []:
        if "extra ==" in requirement:
            continue
        only_elsewhere = "win32" in requirement or "darwin" in requirement
        if ";" in requirement and only_elsewhere and "linux" not in requirement:
            continue
        names.append(_norm(re.split(r"[\s<>=!~;\[(]", requirement, maxsplit=1)[0]))
    return [n for n in names if n in DISTS]


def _closure(name: str, seen: set[str]) -> set[str]:
    if name not in seen and name in DISTS:
        seen.add(name)
        for requirement in _requires(name):
            _closure(requirement, seen)
    return seen


def _mb(names: set[str]) -> float:
    return sum(_size(n) for n in names) / 2**20


def main() -> None:
    everything = _closure("scrinium", set())
    rows = sorted(
        ((_mb(_closure(dep, set())), dep, len(_closure(dep, set())) - 1)
         for dep in _requires("scrinium")),
        reverse=True,
    )  # fmt: skip
    for megabytes, dep, own in rows:
        print(f"{dep:28s} {megabytes:8.1f} MB  (con {own} dependencias propias)")
    print(f"\nTotal sin ventana: {_mb(everything):.1f} MB en {len(everything)} paquetes")
    window = {n for n in WINDOW_PACKAGES if n in DISTS} - everything
    if window:
        print(f"Ventana en Linux (extra window): +{_mb(window):.1f} MB")


if __name__ == "__main__":
    main()
