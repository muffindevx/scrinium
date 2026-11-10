"""Machine memory, without dependencies: total (`scrinium doctor` and Settings) and free (the
memory guard, which pauses background work before the machine runs out)."""

import ctypes
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

_MEMINFO: Final = Path("/proc/meminfo")
_VM_STAT: Final = "/usr/bin/vm_stat"


def _windows_status() -> tuple[int, int] | None:
    """(total, available) on Windows."""

    class _MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("load", ctypes.c_ulong),
            ("total", ctypes.c_ulonglong),
            ("available", ctypes.c_ulonglong),
            ("total_page", ctypes.c_ulonglong),
            ("available_page", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended", ctypes.c_ulonglong),
        ]

    status = _MemoryStatus()
    status.length = ctypes.sizeof(_MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    return int(status.total), int(status.available)


def total_memory_bytes() -> int | None:
    if sys.platform == "win32":
        status = _windows_status()
        return status[0] if status else None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def linux_available(meminfo: str) -> int | None:
    """MemAvailable: what the kernel estimates can be used without resorting to swap."""
    found = re.search(r"^MemAvailable:\s+(\d+) kB", meminfo, re.MULTILINE)
    return int(found[1]) * 1024 if found else None


def macos_available(vm_stat: str) -> int | None:
    """Free, inactive, speculative and purgeable pages: the ones macOS reclaims without swap."""
    size = re.search(r"page size of (\d+) bytes", vm_stat)
    if size is None:
        return None
    pages = 0
    for name in ("free", "inactive", "speculative", "purgeable"):
        found = re.search(rf"^Pages {name}:\s+(\d+)\.", vm_stat, re.MULTILINE)
        if found is None and name in ("free", "inactive"):
            return None
        pages += int(found[1]) if found else 0
    return pages * int(size[1])


def available_memory_bytes() -> int | None:
    """Memory still usable without swap. None if this system does not report it."""
    try:
        if sys.platform == "win32":
            status = _windows_status()
            return status[1] if status else None
        if sys.platform == "darwin":
            output = subprocess.run(  # noqa: S603  fixed system path, no shell
                [_VM_STAT], capture_output=True, text=True, timeout=2, check=True
            ).stdout
            return macos_available(output)
        return linux_available(_MEMINFO.read_text(encoding="ascii"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
