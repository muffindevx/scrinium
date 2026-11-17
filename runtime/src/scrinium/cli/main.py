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
from scrinium.cli.control import attach, link, stop
from scrinium.cli.doctor import doctor
from scrinium.cli.maintenance import UNINSTALL_CONFIRMATION, clean, uninstall
from scrinium.cli.rules import add_commands as add_rule_commands
from scrinium.cli.rules import run as run_rule_command
from scrinium.cli.tools import install_tools, show_tools
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
from scrinium.infrastructure.tools.installer import (
    MODELS_DIRNAME,
    TOOLS_DIRNAME,
    ToolError,
    current_platform,
    locate,
    locate_model,
)
from scrinium.infrastructure.tools.manifest import (
    CHAT_MODEL,
    EMBEDDING_MODEL,
    LLAMA_SERVER,
    MAP_MODEL,
    MODELS,
    TOOLS,
)
from scrinium.main import create_app, open_runtime

_UNSAFE_DATA_DIR_MESSAGES = {
    UnsafeDataDirReason.NOT_ABSOLUTE: "SCRINIUM_DATA_DIR debe ser una ruta absoluta: {path}",
    UnsafeDataDirReason.NOT_A_DIRECTORY: "El directorio de datos no es una carpeta: {path}",
    UnsafeDataDirReason.WRONG_OWNER: (
        "El directorio de datos {path}, o algo dentro de el, pertenece a otra cuenta. "
        "Scrinium no lo usara."
    ),
    UnsafeDataDirReason.PERMISSIONS_IGNORED: (
        "El disco de {path} ignora los permisos de archivo (NTFS o exFAT, por ejemplo): "
        "otras cuentas podrian leer tus datos. Elige con SCRINIUM_DATA_DIR una carpeta en un "
        "disco ext4 o APFS."
    ),
}
_WINDOWS_PERMISSIONS_IGNORED = (
    "Scrinium no pudo dejar {path} solo para tu cuenta: el disco no guarda permisos (FAT, exFAT "
    "o una unidad de red, por ejemplo). Elige con SCRINIUM_DATA_DIR una carpeta en un disco NTFS "
    "de este equipo."
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
        print("No se pudo abrir el navegador. Usa el enlace de arriba.", flush=True)


def serve_with_window(server: uvicorn.Server, sock: socket.socket, launch_url: str) -> None:
    """The server goes to another thread: the window needs the main one. Closing it stops it."""
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        if wait_until_started(server):
            run_window(launch_url)
    except Exception:
        logging.getLogger(__name__).exception("no se pudo abrir la ventana")
        print(
            "No se pudo abrir la ventana de Scrinium. Usa el enlace de arriba en el navegador; "
            "para detener Scrinium, pulsa Ctrl+C.",
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
    "Faltan llama.cpp o los modelos locales: sin ellos, Scrinium no puede buscar por significado "
    "ni responder preguntas. Descargalos con: scrinium tools install"
)
_LLAMA_FAILED = (
    "llama-server no arranco: Scrinium sigue sin modelos locales. Para ver por que, arranca con "
    "--verbose; si falla la GPU, prueba: scrinium tools install --cpu"
)


def start_llama_server(data_dir: Path) -> LlamaServer | None:
    """Starts llama-server with the installed models. None, with a warning, if it cannot."""
    try:
        os_name, arch = current_platform()
    except ToolError as error:
        print(error, file=sys.stderr, flush=True)
        return None
    executable = locate(LLAMA_SERVER, data_dir / TOOLS_DIRNAME, os_name, arch)
    models_dir = data_dir / MODELS_DIRNAME
    served = [
        ServedModel(model, path, embeddings=model is EMBEDDING_MODEL)
        for model in (CHAT_MODEL, EMBEDDING_MODEL)
        if (path := locate_model(model, models_dir)) is not None
    ]
    required = len(served)
    # The map model, under evaluation: served only if installed, and not loaded until
    # someone asks for it.
    if (map_path := locate_model(MAP_MODEL, models_dir)) is not None:
        served.append(ServedModel(MAP_MODEL, map_path, no_thinking=True))
    if executable is None or required < 2:
        print(_MODELS_MISSING, file=sys.stderr, flush=True)
    if executable is None or not served:
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
        print("Scrinium no se ejecuta como root. Usa tu cuenta normal.", file=sys.stderr)
        return 1

    try:
        provider = default_model_provider()
    except ValueError:
        print("SCRINIUM_MODEL_PROVIDER debe ser llama u ollama.", file=sys.stderr)
        return 1
    try:
        ollama_url = check_loopback_url(default_ollama_url())
    except ValueError:
        print(
            "SCRINIUM_OLLAMA_URL debe ser http en este equipo (127.0.0.1 o localhost). "
            "Un servidor remoto seria una salida de datos y no esta permitido.",
            file=sys.stderr,
        )
        return 1

    try:
        sock = bind_loopback(port)
    except OSError:
        if scrinium_listens_on(port):
            message = (
                "Scrinium ya esta abierto: busca su ventana o la terminal donde lo arrancaste. "
                "Para abrir otro a la vez, usa otro puerto con --port."
            )
        else:
            message = f"Otro programa usa el puerto {port}. Elige otro con --port."
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
            "La base de datos de Scrinium es de una version mas nueva. Actualiza Scrinium.",
            file=sys.stderr,
        )
        return 1
    if not config.web_dist.is_dir():
        print(
            "Esta copia de Scrinium no incluye la interfaz: solo responde la API. Instala el "
            "paquete publicado o, desde el codigo fuente, compilala con `make build`.",
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

    print(f"Scrinium {__version__} en {config.allowed_origin} (solo este equipo)", flush=True)
    if interface == Interface.WINDOW:
        print("Scrinium esta en su ventana. Cierrala para detenerlo.", flush=True)
    else:
        print(
            "Scrinium sigue en marcha mientras esta terminal este abierta. Para detenerlo, "
            "pulsa Ctrl+C.",
            flush=True,
        )
    minutes = int(LAUNCH_TOKEN_TTL_SECONDS // 60)
    print(
        f"Si la interfaz no se abre, usa este enlace de un solo uso (caduca en {minutes} "
        f"minutos):\n{launch_url}",
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

    start_parser = commands.add_parser("start", help="Inicia Scrinium y abre la interfaz local.")
    try:
        port = default_port()
    except ValueError:
        parser.error("SCRINIUM_PORT debe ser un numero")
    start_parser.add_argument("--port", type=int, default=port)
    shown = start_parser.add_mutually_exclusive_group()
    shown.add_argument(
        "--browser",
        dest="interface",
        action="store_const",
        const=Interface.BROWSER,
        help="Abrir el navegador en vez de la ventana de Scrinium.",
    )
    shown.add_argument(
        "--no-browser",
        dest="interface",
        action="store_const",
        const=Interface.NONE,
        help="No abrir ninguna interfaz; solo el enlace en la terminal.",
    )
    start_parser.set_defaults(interface=Interface.WINDOW)
    start_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Mostrar cada peticion y los detalles."
    )

    tools_parser = commands.add_parser(
        "tools", help="Lo que Scrinium descarga: gitleaks, llama.cpp y los modelos locales."
    )
    tools_commands = tools_parser.add_subparsers(dest="tools_command", required=True)
    install_parser = tools_commands.add_parser(
        "install", help="Descarga lo que falte, con version y SHA256 fijos."
    )
    install_parser.add_argument(
        "names", nargs="*", choices=sorted([*TOOLS, *MODELS]), metavar="nombre"
    )
    install_parser.add_argument(
        "--cpu", action="store_true", help="llama.cpp solo con CPU, aunque haya Vulkan."
    )
    tools_commands.add_parser("status", help="Muestra que esta instalado y donde.")

    commands.add_parser("doctor", help="Comprueba el equipo y dice como arreglar lo que falte.")
    commands.add_parser("stop", help="Detiene todos los Scrinium en marcha y su llama-server.")
    attach_parser = commands.add_parser(
        "attach", help="Muestra en vivo todo lo que hace el Scrinium en marcha (modo dios)."
    )
    attach_parser.add_argument("--port", type=int, help="El de ese puerto, si hay varios.")
    attach_parser.add_argument(
        "--content",
        action="store_true",
        help="Tambien preguntas, prompts y respuestas del modelo. Los secretos se ocultan.",
    )
    link_parser = commands.add_parser(
        "link", help="Pide al Scrinium en marcha un enlace de entrada nuevo, sin reiniciarlo."
    )
    link_parser.add_argument("--port", type=int, help="El de ese puerto, si hay varios.")
    link_parser.add_argument("--open", action="store_true", help="Abrirlo tambien en el navegador.")
    clean_parser = commands.add_parser(
        "clean", help="Borra restos y versiones viejas. No toca proyectos ni reglas."
    )
    clean_parser.add_argument("--yes", action="store_true", help="No pedir confirmacion.")
    uninstall_parser = commands.add_parser(
        "uninstall", help="Borra todo Scrinium de este equipo: datos, modelos y el programa."
    )
    uninstall_parser.add_argument(
        "--yes", action="store_true", help=f"No pedir que se escriba {UNINSTALL_CONFIRMATION}."
    )

    add_rule_commands(commands)

    args = parser.parse_args(argv)
    logging.captureWarnings(True)
    if args.command in {"check", "hooks"}:
        return run_rule_command(args)
    if args.command == "rules":
        configure_logging(verbose=False)
        return 1 if _is_root() else run_rule_command(args)
    if args.command == "tools":
        return tools(args)
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
        parser.error("--port debe estar entre 0 y 65535")
    return start(args.port, args.interface, args.verbose)


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() == 0:
        print("Scrinium no se ejecuta como root. Usa tu cuenta normal.", file=sys.stderr)
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
        return install_tools(data_dir, args.names, args.cpu)
    return show_tools(data_dir)


if __name__ == "__main__":
    sys.exit(main())
