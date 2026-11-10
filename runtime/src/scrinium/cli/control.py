"""`scrinium stop` and `scrinium attach`: they talk to running instances over /control/."""

import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final

from scrinium.api.middleware import CONTROL_HEADER
from scrinium.core import instances
from scrinium.core.instances import Instance

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
            print(
                f"No habia ningun Scrinium en marcha. Se detuvieron {orphans} llama-server sueltos."
            )
        else:
            print("No hay ningun Scrinium en marcha.")
        return 0
    failed = False
    for instance in alive:
        print(f"Deteniendo Scrinium en el puerto {instance.port}…", flush=True)
        asked = _ask_to_stop(instance)
        if not (asked and instances.wait_exit(instance.pid, _STOP_TIMEOUT_SECONDS)):
            # It did not answer or finish in time: force it, only if the PID is still its own.
            if instances.is_process_of(instance.pid, "scrinium"):
                instances.force_stop(instance.pid)
            if instances.pid_alive(instance.pid):
                print(f"  No se pudo detener el proceso {instance.pid}.", file=sys.stderr)
                failed = True
                continue
        _stop_llama(instance.llama_pid)
        instances.unregister(data_dir, instance.port)
        print("  Detenido.")
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
        where = f" en el puerto {port}" if port is not None else ""
        print(
            f"No hay ningun Scrinium en marcha{where}. Arrancalo con: scrinium start",
            file=sys.stderr,
        )
        return 1
    if content:
        print(
            "Aviso: --contenido muestra las preguntas, los prompts y el codigo que va al modelo. "
            "Lo que salga aqui puede quedar en el historial de la terminal o en capturas.",
            file=sys.stderr,
            flush=True,
        )
    path = "/control/logs?contenido=true" if content else "/control/logs"
    print(
        f"Enganchado a Scrinium {instance.version} en el puerto {instance.port}. "
        "Ctrl+C suelta la consola; Scrinium sigue en marcha.",
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
        print("\nConsola suelta. Scrinium sigue en marcha.")
        return 0
    except (urllib.error.URLError, OSError):
        print("Se perdio la conexion con Scrinium.", file=sys.stderr)
        return 1
    print("Scrinium se detuvo.")
    return 0
