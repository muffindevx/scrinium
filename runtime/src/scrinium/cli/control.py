"""`scrinium stop`, `attach` and `link`: they talk to running instances over /control/."""

import json
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Final

from scrinium.api.middleware import CONTROL_HEADER
from scrinium.core import instances
from scrinium.core.instances import Instance
from scrinium.core.session import LAUNCH_TOKEN_TTL_SECONDS

_STOP_TIMEOUT_SECONDS: Final = 30
# Longer than the server heartbeat: if this passes with nothing, the connection is dead.
_ATTACH_TIMEOUT_SECONDS: Final = 60


def _request(instance: Instance, path: str, method: str) -> urllib.request.Request:
    return urllib.request.Request(  # noqa: S310  always http to 127.0.0.1
        f"{instance.url}{path}", method=method, headers={CONTROL_HEADER: instance.control_token}
    )


def _opener() -> urllib.request.OpenerDirector:
    # No proxies from the environment: the control key must not leave the machine.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _ask_to_stop(instance: Instance) -> bool:
    try:
        with _opener().open(_request(instance, "/control/shutdown", "POST"), timeout=5):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _stop_llama(pid: int | None) -> bool:
    """A llama-server that outlived its Scrinium (on Windows or macOS, if it died abruptly)."""
    if (
        pid is None
        or not instances.pid_alive(pid)
        or not instances.is_process_of(pid, "llama-server")
    ):
        return False
    instances.force_stop(pid)
    return True


def stop(data_dir: Path) -> int:
    recorded = instances.recorded(data_dir)
    alive = [instance for instance in recorded if instances.pid_alive(instance.pid)]
    if not alive:
        orphans = sum(_stop_llama(instance.llama_pid) for instance in recorded)
        for instance in recorded:
            instances.unregister(data_dir, instance.port)
        if orphans:
            print(f"No Scrinium was running. Stopped {orphans} stray llama-server processes.")
        else:
            print("No Scrinium is running.")
        return 0
    failed = False
    for instance in alive:
        print(f"Stopping Scrinium on port {instance.port}…", flush=True)
        asked = _ask_to_stop(instance)
        if not (asked and instances.wait_exit(instance.pid, _STOP_TIMEOUT_SECONDS)):
            # It did not answer or finish in time: force it, only if the PID is still its own.
            if instances.is_process_of(instance.pid, "scrinium"):
                instances.force_stop(instance.pid)
            if instances.pid_alive(instance.pid):
                print(f"  Process {instance.pid} could not be stopped.", file=sys.stderr)
                failed = True
                continue
        _stop_llama(instance.llama_pid)
        instances.unregister(data_dir, instance.port)
        print("  Stopped.")
    for instance in recorded:
        if instance not in alive:
            _stop_llama(instance.llama_pid)
            instances.unregister(data_dir, instance.port)
    return 1 if failed else 0


def _choose(data_dir: Path, port: int | None) -> Instance | None:
    alive = instances.running(data_dir)
    if port is not None:
        return next((instance for instance in alive if instance.port == port), None)
    return alive[0] if alive else None


def attach(data_dir: Path, port: int | None, content: bool) -> int:
    instance = _choose(data_dir, port)
    if instance is None:
        where = f" on port {port}" if port is not None else ""
        print(
            f"No Scrinium is running{where}. Start it with: scrinium start",
            file=sys.stderr,
        )
        return 1
    if content:
        print(
            "Warning: --content shows the questions, prompts and code sent to the model. "
            "What appears here may stay in the terminal history or in screenshots.",
            file=sys.stderr,
            flush=True,
        )
    path = "/control/logs?content=true" if content else "/control/logs"
    print(
        f"Attached to Scrinium {instance.version} on port {instance.port}. "
        "Ctrl+C detaches; Scrinium keeps running.",
        flush=True,
    )
    try:
        with _opener().open(
            _request(instance, path, "GET"), timeout=_ATTACH_TIMEOUT_SECONDS
        ) as stream:
            for raw in stream:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line:
                    print(line, flush=True)
    except KeyboardInterrupt:
        print("\nDetached. Scrinium keeps running.")
        return 0
    except (urllib.error.URLError, OSError):
        print("The connection to Scrinium was lost.", file=sys.stderr)
        return 1
    print("Scrinium stopped.")
    return 0


def request_launch_link(instance: Instance) -> str | None:
    """A new single-use entry link from a running instance; None if it does not answer."""
    try:
        with _opener().open(
            _request(instance, "/control/launch-link", "POST"), timeout=5
        ) as response:
            url = json.loads(response.read(4096)).get("url")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return url if isinstance(url, str) and url.startswith(instance.url) else None


def link(data_dir: Path, port: int | None, open_browser: bool) -> int:
    instance = _choose(data_dir, port)
    if instance is None:
        where = f" on port {port}" if port is not None else ""
        print(
            f"No Scrinium is running{where}. Start it with: scrinium start",
            file=sys.stderr,
        )
        return 1
    url = request_launch_link(instance)
    if url is None:
        print(
            f"Scrinium on port {instance.port} did not answer. If it is still stuck: scrinium stop",
            file=sys.stderr,
        )
        return 1
    minutes = int(LAUNCH_TOKEN_TTL_SECONDS // 60)
    print(
        f"One-time entry link (it expires in {minutes} minutes). Using it closes "
        f"the session open in another tab or window:\n{url}",
        flush=True,
    )
    # The link is briefly visible in the process list: see ADR-003.
    if open_browser and not webbrowser.open(url):
        print("The browser could not be opened. Use the link above.", file=sys.stderr)
    return 0
