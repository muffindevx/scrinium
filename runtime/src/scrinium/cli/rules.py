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

LIGHT_COMMANDS: Final = frozenset({"check", "hooks", "mirrors"})
_EXPLAIN_TIMEOUT_SECONDS: Final = 120


def add_commands(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    check_parser = commands.add_parser(
        "check",
        help="Fails if the change touches a verified rule (no model, no network).",
        description=(
            "Matches the changed files against the verified rules in .scrinium/rules/. "
            "Exits with 1 if a verified rule changes value and the change does not update its "
            "file; with 2 if it could not check."
        ),
    )
    check_parser.add_argument(
        "--base", help="Branch or commit to compare against (by default origin/HEAD or main)."
    )
    check_parser.add_argument(
        "--include-local",
        action="store_true",
        help="Also compare the uncommitted changes of the working folder.",
    )
    check_parser.add_argument(
        "--staged",
        action="store_true",
        help="Compare what is staged for the commit (the index), as the pre-commit hook does.",
    )
    check_parser.add_argument(
        "--format", choices=sorted(FORMATS), default="markdown", help="Output format."
    )
    check_parser.add_argument("--output", type=Path, help="Write the output to this file.")
    check_parser.add_argument(
        "--root", type=Path, default=Path("."), help="The source's folder (by default, this one)."
    )
    check_parser.add_argument(
        "--strict",
        action="store_true",
        help="Also fail on rules to review or retired, not only on those that change.",
    )
    check_parser.add_argument(
        "--require-ticket",
        action="store_true",
        help=(
            "Fail if a rule changes and neither a commit touching its file nor the branch name "
            "names a ticket (governance and compliance)."
        ),
    )
    check_parser.add_argument(
        "--explain",
        action="store_true",
        help=(
            "Ask the running Scrinium for one sentence per changed rule (the 'check explanation' "
            "task). Only a comment: it does not change the result."
        ),
    )

    hooks_parser = commands.add_parser(
        "hooks", help="Installs or removes scrinium check as a Git pre-commit hook."
    )
    hooks_commands = hooks_parser.add_subparsers(dest="hooks_command", required=True)
    for name, text in (("install", "Installs it."), ("uninstall", "Removes it.")):
        sub = hooks_commands.add_parser(name, help=text)
        sub.add_argument("--root", type=Path, default=Path("."), help="The source's folder.")

    mirrors_parser = commands.add_parser(
        "mirrors",
        help="Compares the mirror rules of scrinium.product.yaml (no model, no network).",
        description=(
            "Reads scrinium.product.yaml and, for each pair of mirror rules, compares the values "
            "each one decides with in the current code. Exits with 1 if any pair diverges."
        ),
    )
    mirrors_parser.add_argument(
        "--root", type=Path, default=Path("."), help="The manifest's folder (by default, this one)."
    )

    rules_parser = commands.add_parser(
        "rules", help="Verified rules as files in the repository (.scrinium/rules/)."
    )
    rules_commands = rules_parser.add_subparsers(dest="rules_command", required=True)
    for name, text in (
        ("export", "Writes the reviewed rules of a source into its .scrinium/rules/."),
        ("import", "Rebuilds the reviewed rules from .scrinium/rules/ (a clean clone)."),
    ):
        sub = rules_commands.add_parser(name, help=text)
        sub.add_argument("--project", help="Name or id of the project (if there are several).")
        sub.add_argument("--source", help="Name or id of the source (if there are several).")
        sub.add_argument(
            "--dir", dest="folder", type=Path,
            help="Another rules folder (by default, the source's .scrinium/rules/).",
        )  # fmt: skip


def run(args: argparse.Namespace) -> int:
    if args.command == "check":
        return run_check(args)
    if args.command == "hooks":
        return run_hooks(args)
    if args.command == "mirrors":
        return run_mirrors(args)
    return run_rules(args)


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
        print("--explain: no Scrinium is running; no explanation.", file=sys.stderr)
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
        print("--explain: Scrinium did not answer; no explanation.", file=sys.stderr)
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
            require_ticket=args.require_ticket,
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


# --- mirrors ----------------------------------------------------------------------------------


def run_mirrors(args: argparse.Namespace) -> int:
    from scrinium.application.check.product import (
        MANIFEST,
        compare_mirrors,
        describe,
        load_manifest,
    )
    from scrinium.application.check.yaml_subset import FileFormatError

    try:
        manifest = load_manifest(args.root.resolve())
    except (FileFormatError, OSError) as error:
        print(f"scrinium mirrors: {error}", file=sys.stderr)
        return 2
    if manifest is None:
        print(f"No {MANIFEST} in {args.root.resolve()}.", file=sys.stderr)
        return 2
    report = compare_mirrors(manifest)
    for problem in report.errors:
        print(f"Error: {problem}", file=sys.stderr)
    if report.divergent:
        print(f"{manifest.product}: {len(report.divergent)} of {report.pairs} pairs diverge.")
        for first, second in report.divergent:
            print(f"- {describe(first, second)}")
    else:
        print(f"{manifest.product}: {report.pairs} pairs of mirror rules, none diverges.")
    return 2 if report.errors else 1 if report.divergent else 0


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
                "created": "Pre-commit hook created: scrinium check runs before each commit.",
                "added": "scrinium check added at the start of the existing pre-commit hook.",
                "present": "scrinium check was already in the pre-commit hook.",
            }
        else:
            done = uninstall(repository)
            messages = {
                "deleted": "Pre-commit hook deleted (it only had scrinium check).",
                "removed": "scrinium check removed from the pre-commit hook; the rest stays.",
                "absent": "scrinium check was not in the pre-commit hook.",
            }
    except (GitError, HookError, OSError) as error:
        print(f"scrinium hooks: {error}", file=sys.stderr)
        return 1
    print(messages[done])
    return 0


# --- rules export | import -------------------------------------------------------------------


def run_rules(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from scrinium.application.check.rule_files import RuleFileError, load
    from scrinium.application.ingestion.sources import settings_of
    from scrinium.application.knowledge.rulebook import import_rules, reviewed_rules
    from scrinium.core.config import default_data_dir
    from scrinium.infrastructure.sqlite.database import DATABASE_FILENAME, Database
    from scrinium.infrastructure.sqlite.models import Project, SourceConnection

    data_dir = default_data_dir()
    if not (data_dir / DATABASE_FILENAME).is_file():
        print(f"No Scrinium data in {data_dir}.", file=sys.stderr)
        return 1
    database = Database.open(data_dir)
    with database.session() as session:
        projects = {str(p.id): p.name for p in session.scalars(select(Project))}
        project_id = _pick(projects, args.project, "a project with --project")
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
            "a source with --source",
        )  # fmt: skip
        if source_id is None:
            return 1
        source = sources[source_id]
        root = settings_of(source).root
        folder = args.folder or root / RULES_DIR
        if args.rules_command == "import":
            try:
                files = load(folder)
            except (RuleFileError, OSError) as error:
                print(f"scrinium rules import: {error}", file=sys.stderr)
                return 2
            project = session.get_one(Project, uuid.UUID(project_id))
            done = import_rules(session, project, source, files)
            print(
                f"{len(files)} rules read from {folder}: {len(done.created)} created, "
                f"{len(done.updated)} updated, {len(done.unchanged)} unchanged"
                + (f", {len(done.skipped)} skipped" if done.skipped else "")
                + "."
            )
            if done.unanchored:
                print(
                    f"{len(done.unanchored)} rules have anchors no longer in the code: "
                    "review them in the console.",
                    file=sys.stderr,
                )
            return 0
        rules = reviewed_rules(session, uuid.UUID(project_id), source.id)
    if args.folder is None and not root.is_dir():
        print(f"The source's folder does not exist on this machine: {root}", file=sys.stderr)
        return 1
    written, unchanged, removed = write_all(folder, rules)
    tombstones = sum(1 for rule in rules if rule.is_tombstone)
    print(
        f"{len(rules) - tombstones} verified and {tombstones} rejected rules in {folder}: "
        f"{written} written, {unchanged} unchanged, {removed} removed."
    )
    return 0


def _pick(names: dict[str, str], wanted: str | None, what: str) -> str | None:
    """The id among `names` (id to name) that `wanted` names, or the only one."""
    found = [key for key, name in names.items() if wanted is None or wanted in {key, name}]
    if len(found) == 1:
        return found[0]
    listed = ", ".join(sorted(names.values())) or "none"
    print(f"Choose {what}: {listed}", file=sys.stderr)
    return None


def main(argv: Sequence[str]) -> int:
    """The light commands alone, without importing the server."""
    from scrinium.core.language import use_english

    use_english()
    parser = argparse.ArgumentParser(prog="scrinium")
    add_commands(parser.add_subparsers(dest="command", required=True))
    return run(parser.parse_args(argv))
