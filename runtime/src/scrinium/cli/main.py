"""Scrinium's command line."""

import argparse
import contextlib
import logging
import os
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Sequence
from dataclasses import replace
from enum import StrEnum
from pathlib import Path

import uvicorn

from scrinium import __version__
from scrinium.application.analysis import extraction
from scrinium.application.privacy.erasure import retry_pending_purges
from scrinium.cli.control import attach, link, stop
from scrinium.cli.doctor import doctor
from scrinium.cli.maintenance import UNINSTALL_CONFIRMATION, clean, uninstall
from scrinium.cli.rules import add_commands as add_rule_commands
from scrinium.cli.rules import run as run_rule_command
from scrinium.cli.tools import install_tools, move_tools, show_tools
from scrinium.cli.window import close_window, quiet_window_engine, run_window, window_available
from scrinium.core import instances
from scrinium.core.config import (
    LOOPBACK_HOST,
    ModelProvider,
    RuntimeConfig,
    default_data_dir,
    default_embedding_model,
    default_llm_model,
    default_model_provider,
    default_ollama_url,
    default_port,
)
from scrinium.core.data_dir import UnsafeDataDirError, UnsafeDataDirReason, prepare_data_dir
from scrinium.core.log_hub import HUB
from scrinium.core.logging import configure_logging
from scrinium.core.session import LAUNCH_TOKEN_TTL_SECONDS, LocalSessionManager
from scrinium.infrastructure.llama_server import LLAMA_DIRNAME, LlamaServer, ServedModel
from scrinium.infrastructure.local_http import ModelStatus, check_loopback_url
from scrinium.infrastructure.sqlite.database import IncompatibleDatabaseError
from scrinium.infrastructure.tools import downloads
from scrinium.infrastructure.tools.installer import (
    ToolError,
    current_platform,
    locate,
    locate_model,
)
from scrinium.infrastructure.tools.manifest import (
    ADDONS,
    CHAT_MODEL,
    EMBEDDING_MODEL,
    LLAMA_SERVER,
    LOCAL_ADDON,
    MAP_MODEL,
    MODELS,
    TOOLS,
)
from scrinium.main import create_app, open_runtime

_UNSAFE_DATA_DIR_MESSAGES = {
    UnsafeDataDirReason.NOT_ABSOLUTE: "SCRINIUM_DATA_DIR must be an absolute path: {path}",
    UnsafeDataDirReason.NOT_A_DIRECTORY: "The data directory is not a folder: {path}",
    UnsafeDataDirReason.WRONG_OWNER: (
        "The data directory {path}, or something inside it, belongs to another account. "
        "Scrinium will not use it."
    ),
    UnsafeDataDirReason.PERMISSIONS_IGNORED: (
        "The disk of {path} ignores file permissions (NTFS or exFAT, for example): "
        "other accounts could read your data. Choose a folder on an ext4 or APFS disk with "
        "SCRINIUM_DATA_DIR."
    ),
}
_WINDOWS_PERMISSIONS_IGNORED = (
    "Scrinium could not make {path} private to your account: the disk keeps no permissions (FAT, "
    "exFAT or a network drive, for example). Choose a folder on an NTFS disk of this machine "
    "with SCRINIUM_DATA_DIR."
)


def unsafe_data_dir_message(reason: UnsafeDataDirReason, platform: str = sys.platform) -> str:
    if platform == "win32" and reason == UnsafeDataDirReason.PERMISSIONS_IGNORED:
        return _WINDOWS_PERMISSIONS_IGNORED
    return _UNSAFE_DATA_DIR_MESSAGES[reason]


def bind_loopback(port: int) -> socket.socket:
    """Reserves the port before issuing the token, so it is not handed to another program."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((LOOPBACK_HOST, port))
    except OSError:
        sock.close()
        raise
    return sock


def scrinium_listens_on(port: int) -> bool:
    """Whether the busy port answers like Scrinium. It only asks this machine."""
    try:
        with urllib.request.urlopen(f"http://{LOOPBACK_HOST}:{port}/health", timeout=1) as response:
            return b'"service":"scrinium"' in response.read(200)
    except (OSError, urllib.error.URLError):
        return False


class Interface(StrEnum):
    WINDOW = "window"
    BROWSER = "browser"
    NONE = "none"


def choose_interface(requested: Interface, can_open_window: bool) -> Interface:
    """Without a window available, the browser is used: Scrinium never fails to start over it."""
    if requested == Interface.WINDOW and not can_open_window:
        return Interface.BROWSER
    return requested


def wait_until_started(server: uvicorn.Server) -> bool:
    while not server.started and not server.should_exit:
        time.sleep(0.05)
    return server.started


def open_when_ready(server: uvicorn.Server, launch_url: str) -> None:
    # The link is briefly visible in the process list: see ADR-003.
    if wait_until_started(server) and not webbrowser.open(launch_url):
        print("The browser could not be opened. Use the link above.", flush=True)


def serve_with_window(server: uvicorn.Server, sock: socket.socket, launch_url: str) -> None:
    """The server goes to another thread: the window needs the main one. Closing it stops it."""
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        if wait_until_started(server):
            run_window(launch_url)
    except Exception:
        logging.getLogger(__name__).exception("the window could not be opened")
        print(
            "The Scrinium window could not be opened. Use the link above in the browser; "
            "to stop Scrinium, press Ctrl+C.",
            flush=True,
        )
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        HUB.close_all()
        server.should_exit = True
        thread.join(timeout=10)


_MODELS_MISSING = (
    "llama.cpp or the search model is missing: without them, Scrinium cannot search by "
    "meaning. Download them with: scrinium tools install"
)
_LLAMA_FAILED = (
    "llama-server did not start: Scrinium goes on without local models. To see why, start with "
    "--verbose; if the GPU fails, try: scrinium tools install --cpu"
)


def start_llama_server(data_dir: Path) -> LlamaServer | None:
    """Starts llama-server with the installed models. None, with a warning, if it cannot."""
    try:
        os_name, arch = current_platform()
    except ToolError as error:
        print(error, file=sys.stderr, flush=True)
        return None
    # The old copy of a move (ADR-015): nobody reads it any more, unless another Scrinium on
    # this data directory is still running.
    if not instances.running(data_dir):
        downloads.finish_cleanup(data_dir)
    tools_dir = downloads.tools_dir(data_dir)
    executable = locate(LLAMA_SERVER, tools_dir, os_name, arch)
    models_dir = downloads.models_dir(data_dir)
    served = [
        ServedModel(model, path, embeddings=model is EMBEDDING_MODEL)
        for model in (CHAT_MODEL, EMBEDDING_MODEL)
        if (path := locate_model(model, models_dir)) is not None
    ]
    # ADR-013: the embedding model is required; the chat model is the optional local add-on.
    has_embeddings = any(entry.embeddings for entry in served)
    # The map model, under evaluation: served only if installed, and not loaded until
    # someone asks for it.
    if (map_path := locate_model(MAP_MODEL, models_dir)) is not None:
        served.append(ServedModel(MAP_MODEL, map_path, no_thinking=True))
    if executable is None or not has_embeddings:
        print(_MODELS_MISSING, file=sys.stderr, flush=True)
    if executable is None or not served:
        return None
    try:
        tree = tools_dir / Path(*executable.relative_to(tools_dir).parts[:2])
        downloads.verify(data_dir, tree, full=True)
        for entry in served:
            downloads.verify(data_dir, entry.path, full=False)
    except ToolError as error:
        print(f"{error} Scrinium goes on without local models.", file=sys.stderr, flush=True)
        return None
    server = LlamaServer(executable, served, data_dir / LLAMA_DIRNAME)
    if not server.start():
        print(_LLAMA_FAILED, file=sys.stderr, flush=True)
        return None
    return server


def start(port: int, interface: Interface, verbose: bool = False) -> int:
    configure_logging(verbose)

    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() == 0:
        print("Scrinium does not run as root. Use your normal account.", file=sys.stderr)
        return 1

    try:
        provider = default_model_provider()
    except ValueError:
        print("SCRINIUM_MODEL_PROVIDER must be llama or ollama.", file=sys.stderr)
        return 1
    try:
        ollama_url = check_loopback_url(default_ollama_url())
    except ValueError:
        print(
            "SCRINIUM_OLLAMA_URL must be http on this machine (127.0.0.1 or localhost). "
            "A remote server would be a data egress and is not allowed.",
            file=sys.stderr,
        )
        return 1

    try:
        sock = bind_loopback(port)
    except OSError:
        if scrinium_listens_on(port):
            message = (
                "Scrinium is already open: look for its window or the terminal that started it. "
                "To open another one at the same time, use another port with --port."
            )
        else:
            message = f"Another program uses port {port}. Choose another with --port."
        print(message, file=sys.stderr)
        return 1

    config = RuntimeConfig(
        port=sock.getsockname()[1],
        data_dir=default_data_dir(),
        model_provider=provider,
        ollama_url=ollama_url,
        embedding_model=default_embedding_model(),
        llm_model=default_llm_model(),
    )
    try:
        database, environment = open_runtime(config)
    except UnsafeDataDirError as error:
        sock.close()
        message = unsafe_data_dir_message(error.reason).format(path=config.data_dir)
        print(message, file=sys.stderr)
        return 1
    except IncompatibleDatabaseError:
        sock.close()
        print(
            "Scrinium's database is from a newer version. Update Scrinium.",
            file=sys.stderr,
        )
        return 1
    if not config.web_dist.is_dir():
        print(
            "This copy of Scrinium does not include the interface: only the API answers. "
            "Install the published package or, from the source code, build it with `make build`.",
            file=sys.stderr,
            flush=True,
        )
    llama = None
    if config.model_provider == ModelProvider.LLAMA:
        llama = start_llama_server(config.data_dir)
        if llama is not None:
            config = replace(config, llama_url=llama.url, llama_api_key=llama.api_key)
    control_token = secrets.token_urlsafe(32)
    config = replace(config, control_token=control_token)
    sessions = LocalSessionManager()
    launch_url = f"{config.allowed_origin}/auth/launch?token={sessions.issue_launch_token()}"

    app = create_app(config, sessions, database, environment)
    # Jobs a closed Scrinium left halfway (phase 9.2): back to the queue, then run.
    app.state.jobs.recover()
    app.state.ingestion.submit_jobs()
    # Purges a failed deletion left to do (R3): before anything else reads the files.
    if retry_pending_purges(database, app.state.ingestion.indexer):
        print("Purges from an earlier deletion are still pending; they will be retried at startup.",
              file=sys.stderr, flush=True)  # fmt: skip
    # Rule extractions left halfway at shutdown: they resume by themselves.
    if app.state.analyses.llm.status() is ModelStatus.READY:
        extraction.resume_pending(database, app.state.analyses.llm, app.state.analyses)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_config=None,
            # The uvicorn access log would record the query string, and the token with it.
            access_log=False,
            server_header=False,
            proxy_headers=False,
            ws="none",
            timeout_graceful_shutdown=3,
        )
    )

    def request_shutdown() -> None:
        HUB.close_all()
        server.should_exit = True
        close_window()

    app.state.request_shutdown = request_shutdown

    interface = choose_interface(interface, window_available())
    if interface == Interface.WINDOW and not verbose:
        quiet_window_engine()
    if interface == Interface.BROWSER:
        threading.Thread(target=open_when_ready, args=(server, launch_url), daemon=True).start()

    print(f"Scrinium {__version__} at {config.allowed_origin} (this machine only)", flush=True)
    if interface == Interface.WINDOW:
        print("Scrinium is in its window. Close it to stop it.", flush=True)
    else:
        print(
            "Scrinium keeps running while this terminal is open. To stop it, press Ctrl+C.",
            flush=True,
        )
    minutes = int(LAUNCH_TOKEN_TTL_SECONDS // 60)
    print(
        f"If the interface does not open, use this one-time link (it expires in {minutes} "
        f"minutes):\n{launch_url}",
        flush=True,
    )
    instances.register(
        config.data_dir,
        instances.Instance(
            pid=os.getpid(), port=config.port, control_token=control_token,
            version=__version__, started_at=time.time(),
            llama_pid=llama.pid if llama is not None else None,
        ),
    )  # fmt: skip
    try:
        if interface == Interface.WINDOW:
            serve_with_window(server, sock, launch_url)
        else:
            # On exit, uvicorn re-raises the signal with the previous handler: with the
            # default one, SIGTERM would kill it before `finally`, leaving its registry entry.
            signal.signal(signal.SIGTERM, lambda *_: setattr(server, "should_exit", True))
            # Ctrl+C already stops the server in order: no traceback needed.
            with contextlib.suppress(KeyboardInterrupt):
                server.run(sockets=[sock])
    finally:
        instances.unregister(config.data_dir, config.port)
        sock.close()
        app.state.ingestion.shutdown()
        app.state.questions.shutdown()
        app.state.analyses.shutdown()
        if llama is not None:
            llama.stop()
        database.dispose()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scrinium", description="Scrinium, Local-first.")
    parser.add_argument("--version", action="version", version=f"scrinium {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    start_parser = commands.add_parser(
        "start", help="Starts Scrinium and opens the local interface."
    )
    try:
        port = default_port()
    except ValueError:
        parser.error("SCRINIUM_PORT must be a number")
    start_parser.add_argument("--port", type=int, default=port)
    shown = start_parser.add_mutually_exclusive_group()
    shown.add_argument(
        "--browser",
        dest="interface",
        action="store_const",
        const=Interface.BROWSER,
        help="Open the browser instead of the Scrinium window.",
    )
    shown.add_argument(
        "--no-browser",
        dest="interface",
        action="store_const",
        const=Interface.NONE,
        help="Open no interface; only the link in the terminal.",
    )
    start_parser.set_defaults(interface=Interface.WINDOW)
    start_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show every request and the details."
    )

    tools_parser = commands.add_parser(
        "tools", help="What Scrinium downloads: gitleaks, llama.cpp and the search model."
    )
    tools_commands = tools_parser.add_subparsers(dest="tools_command", required=True)
    install_parser = tools_commands.add_parser(
        "install", help="Downloads what is missing, with pinned versions and SHA256."
    )
    install_parser.add_argument(
        "names",
        nargs="*",
        choices=sorted([*TOOLS, *MODELS, *ADDONS]),
        metavar="name",
        help=(
            "Without names: gitleaks, llama.cpp and the search model. "
            f"'{LOCAL_ADDON}' adds the local chat model (2.5 GB)."
        ),
    )
    install_parser.add_argument(
        "--cpu", action="store_true", help="CPU-only llama.cpp, even if Vulkan is available."
    )
    install_parser.add_argument(
        "--dir",
        type=Path,
        metavar="folder",
        help=(
            "Installs into this folder (and moves what is already downloaded there). "
            "See: scrinium tools move."
        ),
    )
    tools_commands.add_parser("status", help="Shows what is installed and where.")
    move_parser = tools_commands.add_parser(
        "move",
        help=(
            "Moves the downloads (llama.cpp and models) to another folder, for example a bigger "
            "disk. Your data stays in the data directory."
        ),
    )
    move_parser.add_argument("dir", type=Path, metavar="folder")

    commands.add_parser("doctor", help="Checks the machine and says how to fix what is missing.")
    coverage_parser = commands.add_parser(
        "coverage", help="Where the code decides and which decisions a verified rule covers."
    )
    coverage_parser.add_argument("--root", type=Path, default=Path(), help="The repository.")
    coverage_parser.add_argument("--path", default="", help="A folder or file of the repository.")
    coverage_parser.add_argument("--json", action="store_true", help="JSON output.")
    mcp_parser = commands.add_parser(
        "mcp", help="MCP server (stdio) with this repository's rules, for agents."
    )
    mcp_parser.add_argument(
        "--project", help="The Scrinium project (by default, the one of --root)."
    )
    mcp_parser.add_argument(
        "--root", type=Path, default=Path(), help="The repository (by default, this folder)."
    )
    demo_parser = commands.add_parser(
        "demo",
        help="5-minute demo: verified rules, a change that breaks one, and the check's warning.",
    )
    demo_parser.add_argument(
        "--dir", type=Path, default=Path("scrinium-demo"),
        help="New folder for the example (by default, ./scrinium-demo).",
    )  # fmt: skip
    commands.add_parser("stop", help="Stops every running Scrinium and its llama-server.")
    attach_parser = commands.add_parser(
        "attach", help="Shows live everything the running Scrinium does."
    )
    attach_parser.add_argument(
        "--port", type=int, help="The one on that port, if there are several."
    )
    attach_parser.add_argument(
        "--content",
        action="store_true",
        help="Also questions, prompts and model answers. Secrets are hidden.",
    )
    link_parser = commands.add_parser(
        "link", help="Asks the running Scrinium for a new entry link, without restarting it."
    )
    link_parser.add_argument("--port", type=int, help="The one on that port, if there are several.")
    link_parser.add_argument("--open", action="store_true", help="Also open it in the browser.")
    clean_parser = commands.add_parser(
        "clean", help="Deletes leftovers and old versions. Never touches projects or rules."
    )
    clean_parser.add_argument("--yes", action="store_true", help="Do not ask for confirmation.")
    uninstall_parser = commands.add_parser(
        "uninstall", help="Removes all of Scrinium from this machine: data, models and the program."
    )
    uninstall_parser.add_argument(
        "--yes", action="store_true", help=f"Do not ask to type {UNINSTALL_CONFIRMATION}."
    )

    add_rule_commands(commands)

    args = parser.parse_args(argv)
    logging.captureWarnings(True)
    if args.command in {"check", "hooks", "mirrors"}:
        return run_rule_command(args)
    if args.command == "rules":
        configure_logging(verbose=False)
        return 1 if _is_root() else run_rule_command(args)
    if args.command == "tools":
        return tools(args)
    if args.command == "coverage":
        from scrinium.cli.coverage import run_coverage

        return run_coverage(args.root, args.path, args.json)
    if args.command == "mcp":
        from scrinium.cli.mcp import main as mcp

        return mcp(args.project, args.root)
    if args.command == "demo":
        from scrinium.cli.demo import demo

        return demo(args.dir)
    if args.command in {"doctor", "stop", "attach", "link", "clean", "uninstall"}:
        configure_logging(verbose=False)
        if _is_root():
            return 1
        data_dir = default_data_dir()
        if args.command == "doctor":
            return doctor(data_dir)
        if args.command == "stop":
            return stop(data_dir)
        if args.command == "attach":
            return attach(data_dir, args.port, args.content)
        if args.command == "link":
            return link(data_dir, args.port, args.open)
        if args.command == "clean":
            return clean(data_dir, args.yes)
        return uninstall(data_dir, args.yes)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    return start(args.port, args.interface, args.verbose)


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() == 0:
        print("Scrinium does not run as root. Use your normal account.", file=sys.stderr)
        return True
    return False


def tools(args: argparse.Namespace) -> int:
    configure_logging(verbose=False)
    if _is_root():
        return 1
    data_dir = default_data_dir()
    try:
        prepare_data_dir(data_dir)
    except UnsafeDataDirError as error:
        print(unsafe_data_dir_message(error.reason).format(path=data_dir), file=sys.stderr)
        return 1
    if args.tools_command == "install":
        return install_tools(data_dir, args.names, args.cpu, args.dir)
    if args.tools_command == "move":
        return move_tools(data_dir, args.dir)
    return show_tools(data_dir)


if __name__ == "__main__":
    sys.exit(main())
