"""`scrinium check`, `scrinium hooks` and `scrinium rules export` (phase 2).

`check` and `hooks` start fast and need no Scrinium data: the entry point (`cli/entry.py`)
sends them here before importing the server. `rules export` reads the local database.
"""

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from scrinium.application.check.engine import CHANGED, REVIEW, CheckResult, check
from scrinium.application.check.git import GitError, Reader, Repository
from scrinium.application.check.hooks import HookError, install, uninstall
from scrinium.application.check.report import FORMATS
from scrinium.application.check.rule_files import RULES_DIR, write_all

LIGHT_COMMANDS: Final = frozenset({"check", "hooks"})
_EXPLAIN_TIMEOUT_SECONDS: Final = 120


def add_commands(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    check_parser = commands.add_parser(
        "check",
        help="Falla si el cambio toca una regla verificada (sin modelo ni red).",
        description=(
            "Cruza los archivos cambiados con las reglas verificadas de .scrinium/rules/. "
            "Sale con 1 si una regla verificada cambia de valor sin que el cambio actualice su "
            "archivo; con 2 si no se pudo comprobar."
        ),
    )
    check_parser.add_argument(
        "--base", help="Rama o commit contra el que comparar (por defecto origin/HEAD o main)."
    )
    check_parser.add_argument(
        "--include-local",
        action="store_true",
        help="Comparar también los cambios sin commit de la carpeta de trabajo.",
    )
    check_parser.add_argument(
        "--staged",
        action="store_true",
        help="Comparar lo preparado para el commit (el índice), como hace el hook de pre-commit.",
    )
    check_parser.add_argument(
        "--format", choices=sorted(FORMATS), default="markdown", help="Formato de la salida."
    )
    check_parser.add_argument("--output", type=Path, help="Escribir la salida en este archivo.")
    check_parser.add_argument(
        "--root", type=Path, default=Path("."), help="Carpeta de la fuente (por defecto, esta)."
    )
    check_parser.add_argument(
        "--strict",
        action="store_true",
        help="Fallar también con reglas para revisar o retiradas, no solo con las que cambian.",
    )
    check_parser.add_argument(
        "--explain",
        action="store_true",
        help=(
            "Pedir al Scrinium en marcha una frase por regla que cambia (tarea «Explicación "
            "del check»). Solo un comentario: no cambia el resultado."
        ),
    )

    hooks_parser = commands.add_parser(
        "hooks", help="Instala o quita scrinium check como hook de pre-commit de Git."
    )
    hooks_commands = hooks_parser.add_subparsers(dest="hooks_command", required=True)
    for name, text in (("install", "Lo instala."), ("uninstall", "Lo quita.")):
        sub = hooks_commands.add_parser(name, help=text)
        sub.add_argument("--root", type=Path, default=Path("."), help="Carpeta de la fuente.")

    rules_parser = commands.add_parser(
        "rules", help="Las reglas verificadas como archivos del repositorio (.scrinium/rules/)."
    )
    rules_commands = rules_parser.add_subparsers(dest="rules_command", required=True)
    export_parser = rules_commands.add_parser(
        "export", help="Escribe las reglas verificadas de una fuente en su .scrinium/rules/."
    )
    export_parser.add_argument("--project", help="Nombre o id del proyecto (si hay varios).")
    export_parser.add_argument("--source", help="Nombre o id de la fuente (si hay varias).")
    export_parser.add_argument(
        "--to", type=Path, help="Carpeta de destino (por defecto, .scrinium/rules/ de la fuente)."
    )


def run(args: argparse.Namespace) -> int:
    if args.command == "check":
        return run_check(args)
    if args.command == "hooks":
        return run_hooks(args)
    return run_export(args)


# --- check ------------------------------------------------------------------------------------


def _excerpt(text: str | None, start: int, end: int) -> str:
    if text is None:
        return ""
    return "\n".join(text.splitlines()[start - 1 : end])


def _explain(repository: Repository, result: CheckResult) -> None:
    """Asks the running Scrinium for one sentence per changed rule; any failure is a note."""
    from scrinium.application.check.explain import MAX_ITEMS
    from scrinium.application.knowledge.anchoring import OldAnchor, reanchor
    from scrinium.cli.control import _choose, _opener, _request
    from scrinium.core.config import default_data_dir
    from scrinium.infrastructure.decisions import parse_decisions

    wanted = [f for f in result.findings if f.level in {CHANGED, REVIEW} and f.now][:MAX_ITEMS]
    if not wanted:
        return
    instance = _choose(default_data_dir(), None)
    if instance is None:
        print("--explain: no hay ningún Scrinium en marcha; sin explicación.", file=sys.stderr)
        return
    since = repository.merge_base(result.base)
    before_reader = Reader(repository, since)
    after_reader = repository.reader(result.head)
    items = []
    for finding in wanted:
        if finding.now is None:
            continue
        path, start, end, _ = finding.now
        anchor = finding.anchor
        old_text = before_reader.read(f"{repository.prefix}{anchor.path}")
        # Where the decision was at the base: the excerpt the explanation compares against.
        was = OldAnchor(
            "a", anchor.path, anchor.symbol, anchor.kind, anchor.shape, anchor.value,
            anchor.sketch, anchor.start_line,
        )  # fmt: skip
        old = reanchor([was], {anchor.path: parse_decisions(anchor.path, old_text or "")}).get("a")
        old_start, old_end = (
            (old.decision.start_line, old.decision.end_line)
            if old is not None
            else (anchor.start_line, anchor.end_line)
        )
        items.append(
            {
                "rule_id": finding.rule_id, "title": finding.title[:300], "path": path,
                "start_line": start, "end_line": end,
                "before": _excerpt(old_text, old_start, old_end)[:8000],
                "after": _excerpt(after_reader.read(f"{repository.prefix}{path}"), start, end)[
                    :8000
                ],
            }
        )  # fmt: skip
    request = _request(instance, "/control/check-explain", "POST")
    request.add_header("Content-Type", "application/json")
    request.data = json.dumps({"items": items}).encode()
    try:
        with _opener().open(request, timeout=_EXPLAIN_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        print("--explain: Scrinium no respondió; sin explicación.", file=sys.stderr)
        return
    for item in payload.get("explanations", []):
        for finding in wanted:
            if (
                finding.rule_id == item.get("rule_id")
                and finding.now
                and (
                    finding.now[0] == item.get("path") and finding.now[1] == item.get("start_line")
                )
            ):
                result.explanations[finding.key] = str(item.get("text", ""))[:400]


def run_check(args: argparse.Namespace) -> int:
    try:
        repository = Repository.at(args.root.resolve())
        base = args.base or repository.default_base()
        result = check(
            repository, base, include_local=args.include_local, staged=args.staged,
            strict=args.strict,
        )  # fmt: skip
    except GitError as error:
        print(f"scrinium check: {error}", file=sys.stderr)
        return 2
    if args.explain:
        _explain(repository, result)
    output = FORMATS[args.format](result)
    if args.output is not None:
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    if result.errors:
        return 2
    return 1 if result.failed else 0


# --- hooks ------------------------------------------------------------------------------------


def _command() -> list[str]:
    found = shutil.which("scrinium")
    return [found] if found else [sys.executable, "-m", "scrinium"]


def run_hooks(args: argparse.Namespace) -> int:
    try:
        repository = Repository.at(args.root.resolve())
        if args.hooks_command == "install":
            done = install(repository, _command())
            messages = {
                "created": "Hook de pre-commit creado: scrinium check corre antes de cada commit.",
                "added": "scrinium check añadido al principio del hook de pre-commit existente.",
                "present": "scrinium check ya estaba en el hook de pre-commit.",
            }
        else:
            done = uninstall(repository)
            messages = {
                "deleted": "Hook de pre-commit borrado (solo tenía scrinium check).",
                "removed": "scrinium check quitado del hook de pre-commit; el resto sigue igual.",
                "absent": "scrinium check no estaba en el hook de pre-commit.",
            }
    except (GitError, HookError, OSError) as error:
        print(f"scrinium hooks: {error}", file=sys.stderr)
        return 1
    print(messages[done])
    return 0


# --- rules export -----------------------------------------------------------------------------


def run_export(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from scrinium.application.ingestion.sources import settings_of
    from scrinium.application.knowledge.rulebook import verified_rules
    from scrinium.core.config import default_data_dir
    from scrinium.infrastructure.sqlite.database import DATABASE_FILENAME, Database
    from scrinium.infrastructure.sqlite.models import Project, SourceConnection

    data_dir = default_data_dir()
    if not (data_dir / DATABASE_FILENAME).is_file():
        print(f"No hay datos de Scrinium en {data_dir}.", file=sys.stderr)
        return 1
    database = Database.open(data_dir)
    with database.session() as session:
        projects = {str(p.id): p.name for p in session.scalars(select(Project))}
        project_id = _pick(projects, args.project, "un proyecto con --project")
        if project_id is None:
            return 1
        sources = {
            str(s.id): s
            for s in session.scalars(
                select(SourceConnection).where(SourceConnection.project_id == uuid.UUID(project_id))
            )
        }
        source_id = _pick(
            {key: s.display_name for key, s in sources.items()}, args.source,
            "una fuente con --source",
        )  # fmt: skip
        if source_id is None:
            return 1
        source = sources[source_id]
        rules = verified_rules(session, uuid.UUID(project_id), source.id)
        root = settings_of(source).root
    if args.to is None and not root.is_dir():
        print(f"La carpeta de la fuente no existe en este equipo: {root}", file=sys.stderr)
        return 1
    target = args.to or root / RULES_DIR
    written, unchanged, removed = write_all(target, rules)
    print(
        f"{len(rules)} reglas verificadas en {target}: {written} escritas, {unchanged} sin "
        f"cambios, {removed} quitadas."
    )
    return 0


def _pick(names: dict[str, str], wanted: str | None, what: str) -> str | None:
    """The id among `names` (id to name) that `wanted` names, or the only one."""
    found = [key for key, name in names.items() if wanted is None or wanted in {key, name}]
    if len(found) == 1:
        return found[0]
    listed = ", ".join(sorted(names.values())) or "ninguno"
    print(f"Elige {what}: {listed}", file=sys.stderr)
    return None


def main(argv: Sequence[str]) -> int:
    """The light commands alone, without importing the server."""
    parser = argparse.ArgumentParser(prog="scrinium")
    add_commands(parser.add_subparsers(dest="command", required=True))
    return run(parser.parse_args(argv))
