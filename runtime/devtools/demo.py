"""`make demo`: a Scrinium for trying changes, apart from the real data.

- Its own data directory (`~/.cache/scrinium-demo/data`, or `SCRINIUM_DEMO_DIR`). The tools and
  the models are symbolic links to the real data directory: nothing is copied or downloaded.
- A small example Git repository (`<demo>/repo`) with business rules and two commits, created
  once with fixed authors and dates, so it is the same on every machine.
- On the first run, through the same API the interface uses: the profile «Usuario de pruebas»,
  the project «Demo» with that repository as its source, and its first sync.
- The browser opens already inside, with a link asked for over /control/ (`scrinium link`).
  If the demo is already running, it only opens a new link.

It never reads or writes the real database. Ctrl+C stops it; `rm -rf ~/.cache/scrinium-demo`
starts over (the links do not take the real models with them).

With `--repo` it uses that Git repository instead of the example one (it is only read), and
`--project` names the project. For example, the reference-c reference set, in its own folder:

    SCRINIUM_DEMO_DIR=~/.cache/scrinium-odoo uv run --project runtime python \\
        runtime/devtools/demo.py --port 47631 --repo ~/.cache/scrinium-odoo/repo --project reference-c

Usage: uv run --project runtime python runtime/devtools/demo.py [--port 47630] [--no-browser]
    [--repo PATH] [--project NAME]
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Final

from platformdirs import user_data_dir

from scrinium.application.example import seed_repository
from scrinium.cli.control import request_launch_link
from scrinium.core import instances
from scrinium.core.config import DEFAULT_WEB_DIST, LOOPBACK_HOST
from scrinium.core.data_dir import prepare_data_dir
from scrinium.core.session import LAUNCH_TOKEN_TTL_SECONDS
from scrinium.infrastructure.tools.installer import MODELS_DIRNAME, TOOLS_DIRNAME

DEFAULT_PORT: Final = 47630
PROJECT_NAME: Final = "Demo"
PROFILE_NAME: Final = "Usuario de pruebas"
_START_TIMEOUT_SECONDS: Final = 90


def link_shared_downloads(data_dir: Path, real_data_dir: Path) -> None:
    """The tools and models, linked from the real data directory: no copy of 4 GB."""
    for name in (TOOLS_DIRNAME, MODELS_DIRNAME):
        target, link = real_data_dir / name, data_dir / name
        if link.exists() or link.is_symlink() or not target.is_dir():
            continue
        link.symlink_to(target, target_is_directory=True)
    if not (data_dir / MODELS_DIRNAME).exists():
        print(
            f"No hay modelos en {real_data_dir}: la demo arranca sin IA local. "
            "Descargalos antes con: make tools",
            file=sys.stderr,
            flush=True,
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class _Client:
    """The interface's way in: launch link, session cookie, CSRF and Origin."""

    def __init__(self, port: int, launch_url: str) -> None:
        self.base = f"http://{LOOPBACK_HOST}:{port}"
        # No proxies from the environment: nothing here leaves the machine.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            self._opener.open(launch_url, timeout=10)
            raise RuntimeError("the launch link did not redirect")
        except urllib.error.HTTPError as redirect:
            if redirect.code != 303:
                raise
            cookie = redirect.headers.get("set-cookie", "")
        self._cookie = cookie.split(";", 1)[0]
        self._csrf = self.call("GET", "/api/v1/session")["csrf_token"]

    def call(self, method: str, path: str, body: Any = None) -> Any:
        headers = {"Cookie": self._cookie, "Content-Type": "application/json"}
        if method != "GET":
            headers |= {"Origin": self.base, "X-CSRF-Token": self._csrf}
        request = urllib.request.Request(  # noqa: S310  always http to 127.0.0.1
            f"{self.base}{path}",
            method=method,
            headers=headers,
            data=None if body is None else json.dumps(body).encode(),
        )
        with self._opener.open(request, timeout=30) as response:
            raw = response.read()
        return json.loads(raw) if raw else None


def seed_project(client: _Client, repo: Path, name: str = PROJECT_NAME) -> None:
    projects = client.call("GET", "/api/v1/projects")["projects"]
    if any(project["name"] == name for project in projects):
        return
    client.call("PUT", "/api/v1/profile", {"display_name": PROFILE_NAME})
    project = client.call("POST", "/api/v1/projects", {"name": name})
    client.call(
        "POST",
        f"/api/v1/projects/{project['id']}/sources",
        {"kind": "git_repository", "path": str(repo)},
    )
    print(f"Proyecto {name} creado; su primera sincronizacion ya esta en marcha.", flush=True)


def wait_for_instance(
    data_dir: Path, port: int, process: subprocess.Popen[bytes]
) -> instances.Instance | None:
    deadline = time.monotonic() + _START_TIMEOUT_SECONDS
    while time.monotonic() < deadline and process.poll() is None:
        found = next((i for i in instances.running(data_dir) if i.port == port), None)
        if found is not None:
            try:
                with urllib.request.urlopen(f"{found.url}/ready", timeout=1):  # noqa: S310
                    return found
            except (urllib.error.URLError, OSError):
                pass
        time.sleep(0.2)
    return None


def enter(instance: instances.Instance, open_browser: bool) -> None:
    url = request_launch_link(instance)
    if url is None:
        print(
            "La demo no dio un enlace de entrada. Prueba: uv run --project runtime scrinium link",
            file=sys.stderr,
        )
        return
    if open_browser and webbrowser.open(url):
        print(f"Demo abierta en el navegador ({instance.url}).", flush=True)
    else:
        minutes = int(LAUNCH_TOKEN_TTL_SECONDS // 60)
        print(
            f"Entra en la demo con este enlace (un solo uso, {minutes} minutos):\n{url}", flush=True
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="Solo el enlace, sin abrirlo.")
    parser.add_argument("--repo", type=Path, help="Un repositorio Git propio en vez del ejemplo.")
    parser.add_argument("--project", default=PROJECT_NAME, help="Nombre del proyecto.")
    args = parser.parse_args()

    root = Path(os.environ.get("SCRINIUM_DEMO_DIR", Path.home() / ".cache" / "scrinium-demo"))
    root = root.expanduser().absolute()
    data_dir, repo = root / "data", root / "repo"
    # The real one, whatever SCRINIUM_DATA_DIR says: only its downloads are linked.
    real_data_dir = Path(user_data_dir("scrinium", appauthor=False))
    if data_dir.resolve() == real_data_dir.resolve():
        print("La demo no puede usar el directorio de datos real.", file=sys.stderr)
        return 1
    if not DEFAULT_WEB_DIST.is_dir():
        print("La interfaz no esta compilada: ejecuta antes make build.", file=sys.stderr)
        return 1

    prepare_data_dir(data_dir)
    link_shared_downloads(data_dir, real_data_dir)
    if args.repo is not None:
        repo = args.repo.expanduser().absolute()
        if not (repo / ".git").exists():
            print(f"{repo} no es un repositorio Git.", file=sys.stderr)
            return 1
    else:
        seed_repository(repo)

    running = next((i for i in instances.running(data_dir) if i.port == args.port), None)
    if running is not None:
        print(f"La demo ya esta en marcha en {running.url}.", flush=True)
        enter(running, not args.no_browser)
        return 0

    process = subprocess.Popen(  # noqa: S603  this same Python, no shell
        [sys.executable, "-m", "scrinium.cli.main", "start", "--no-browser",
         "--port", str(args.port)],
        env={**os.environ, "SCRINIUM_DATA_DIR": str(data_dir)},
    )  # fmt: skip
    try:
        instance = wait_for_instance(data_dir, args.port, process)
        if instance is None:
            print("La demo no llego a arrancar.", file=sys.stderr)
            process.terminate()
            return process.wait() or 1
        launch = request_launch_link(instance)
        if launch is not None:
            seed_project(_Client(instance.port, launch), repo, args.project)
        enter(instance, not args.no_browser)
        return process.wait()
    except KeyboardInterrupt:
        # The terminal already sent Ctrl+C to Scrinium too: wait for it to stop in order.
        return process.wait()


if __name__ == "__main__":
    sys.exit(main())
