"""Registry of running Scrinium instances, for `scrinium stop` and `scrinium attach`.

Each instance writes `run/<port>.json` in the data directory (private, 0600): its PID, its
llama-server's, and a new control key. Only the same account can read it, and with it
it can ask the instance to stop or to send it its logs.
"""

import contextlib
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

RUN_DIRNAME: Final = "run"
_PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
_STILL_ACTIVE: Final = 259


@dataclass(frozen=True)
class Instance:
    pid: int
    port: int
    control_token: str
    version: str
    started_at: float
    llama_pid: int | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _record_path(data_dir: Path, port: int) -> Path:
    return data_dir / RUN_DIRNAME / f"{port}.json"


def register(data_dir: Path, instance: Instance) -> None:
    run_dir = data_dir / RUN_DIRNAME
    run_dir.mkdir(mode=0o700, exist_ok=True)
    path = _record_path(data_dir, instance.port)
    # Created already 0600: the key never exists with other permissions.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(asdict(instance), output)


def unregister(data_dir: Path, port: int) -> None:
    with contextlib.suppress(FileNotFoundError):
        _record_path(data_dir, port).unlink()


def recorded(data_dir: Path) -> list[Instance]:
    """Every registered instance, alive or not, from newest to oldest."""
    found = []
    for path in sorted((data_dir / RUN_DIRNAME).glob("*.json")):
        try:
            found.append(Instance(**json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError):
            continue
    return sorted(found, key=lambda instance: instance.started_at, reverse=True)


def running(data_dir: Path) -> list[Instance]:
    return [instance for instance in recorded(data_dir) if pid_alive(instance.pid)]


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and (
                code.value == _STILL_ACTIVE
            )
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return not _is_zombie(pid)


def _is_zombie(pid: int) -> bool:
    """A process that ended but that its parent has not reaped yet does not count as alive."""
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return False
        # The state comes after the name in parentheses, which may contain spaces.
        return stat.rsplit(")", 1)[-1].split()[0] == "Z"
    try:
        result = subprocess.run(  # noqa: S603  system ps, no shell
            ["/bin/ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip().startswith("Z")


def process_command(pid: int) -> str | None:
    """Command line (or executable path) of a process; None if it cannot be known."""
    if sys.platform.startswith("linux"):
        try:
            return (
                Path(f"/proc/{pid}/cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode(errors="replace")
            )
        except OSError:
            return None
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            size = ctypes.c_ulong(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
            return None
        finally:
            kernel32.CloseHandle(handle)
    try:
        result = subprocess.run(  # noqa: S603  system ps, no shell
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def is_process_of(pid: int, needle: str) -> bool:
    """The PID is still the expected process: a reused PID is never killed."""
    command = process_command(pid)
    return command is not None and needle in command


def wait_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.2)
    return not pid_alive(pid)


def _taskkill() -> str:
    return str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe")


def force_stop(pid: int) -> None:
    """Last resort, when a process did not stop gracefully."""
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603  system taskkill, no shell
            [_taskkill(), "/F", "/T", "/PID", str(pid)], capture_output=True, check=False
        )
        return
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    if not wait_exit(pid, 5):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
