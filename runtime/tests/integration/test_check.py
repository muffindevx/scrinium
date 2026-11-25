"""`scrinium check` and `scrinium hooks` on real Git repositories (phase 2.2 and 2.3)."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from scrinium.application.check.engine import CHANGED, MOVED, REMOVED, REVIEW, UPDATED, check
from scrinium.application.check.git import Repository
from scrinium.application.check.hooks import hook_path, install, uninstall
from scrinium.application.check.report import to_json, to_markdown, to_sarif
from scrinium.application.check.rule_files import RULES_DIR, FileAnchor, RuleFile, render
from scrinium.cli.rules import main as cli
from scrinium.infrastructure.decisions import parse_decisions
from tests.integration.test_ingestion import commit_all, git, write

RULE_ID = "0193a5c2-0000-7000-8000-000000000001"
PAGOS = """\
def comision(total):
    nota = 'ventas'
    if total > 100:
        return total * 0.02
    return 0


def otra():
    return 1
"""


def _rule(text: str, path: str = "src/pagos.py", prefix: str = "") -> RuleFile:
    guard = next(d for d in parse_decisions(path, text) if d.kind == "guard")
    anchor = FileAnchor(
        path, guard.symbol, guard.kind, guard.start_line, guard.end_line, guard.shape,
        guard.value, guard.sketch,
    )  # fmt: skip
    return RuleFile(RULE_ID, "Comision por venta", "Mas de 100 pagan un 2 %.", anchors=(anchor,))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "src/pagos.py", PAGOS)
    write(root, f"{RULES_DIR}/{RULE_ID}.md", render(_rule(PAGOS)))
    commit_all(root, "inicio")
    git(root, "checkout", "-q", "-b", "cambio")
    return root


def _check(root: Path, include_local: bool = False, strict: bool = False):  # type: ignore[no-untyped-def]
    return check(Repository.at(root), "main", include_local=include_local, strict=strict)


def _levels(root: Path, include_local: bool = False) -> list[str]:
    return [finding.level for finding in _check(root, include_local).findings]


def test_a_change_beside_the_decision_marks_nothing(repo: Path) -> None:
    write(
        repo, "src/pagos.py", PAGOS.replace("'ventas'", "'otras'").replace("return 1", "return 2")
    )
    commit_all(repo)
    result = _check(repo)
    assert result.findings == [] and not result.failed and result.anchors_checked == 1


def test_a_changed_threshold_fails_until_its_rule_file_changes_too(repo: Path) -> None:
    changed = PAGOS.replace("> 100", "> 150")
    write(repo, "src/pagos.py", changed)
    commit_all(repo)
    result = _check(repo)
    assert [f.level for f in result.findings] == [CHANGED] and result.failed
    sarif = json.loads(to_sarif(result))
    found = sarif["runs"][0]["results"][0]
    assert found["ruleId"] == RULE_ID and found["level"] == "error"
    assert found["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/pagos.py"
    assert json.loads(to_json(result))["failed"] is True
    assert "**fails**" in to_markdown(result)

    # The same change updates the rule's file: acknowledged, not failing.
    write(
        repo,
        f"{RULES_DIR}/{RULE_ID}.md",
        render(_rule(changed)).replace("Mas de 100", "Mas de 150"),
    )
    commit_all(repo)
    result = _check(repo)
    assert result.findings == [] and not result.failed
    # The file changed but its anchor still has the old value: it says so, without failing.
    write(repo, f"{RULES_DIR}/{RULE_ID}.md", render(_rule(PAGOS)).replace("pagan", "cobran"))
    commit_all(repo)
    assert _levels(repo) == [UPDATED] and not _check(repo).failed


def test_local_changes_count_only_when_asked(repo: Path) -> None:
    write(repo, "src/pagos.py", PAGOS.replace("> 100", "> 150"))
    assert _levels(repo) == []
    assert _levels(repo, include_local=True) == [CHANGED]


def test_an_edited_guard_is_to_review_and_a_lost_one_is_removed(repo: Path) -> None:
    write(
        repo, "src/pagos.py", PAGOS.replace("if total > 100:", "if total > 100 and total < 9000:")
    )
    assert _levels(repo, include_local=True) == [REVIEW]
    write(repo, "src/pagos.py", "def otra():\n    return 1\n")
    assert _levels(repo, include_local=True) == [REMOVED]
    assert not _check(repo, include_local=True).failed
    assert _check(repo, include_local=True, strict=True).failed


def test_renames_and_moves_keep_the_rule(repo: Path) -> None:
    git(repo, "mv", "src/pagos.py", "src/cobros.py")
    commit_all(repo)
    assert _levels(repo) == []
    git(repo, "reset", "-q", "--hard", "main")
    # The function moves to another file; the old file stays, with the rest.
    write(repo, "src/pagos.py", "def otra():\n    return 1\n")
    write(repo, "src/comisiones.py", PAGOS.split("\n\n\n")[0] + "\n")
    commit_all(repo)
    assert _levels(repo) == [MOVED] and not _check(repo).failed


def test_a_source_in_a_subfolder_reads_its_own_rules(tmp_path: Path) -> None:
    root = tmp_path / "mono"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "backend/src/pagos.py", PAGOS)
    write(root, f"backend/{RULES_DIR}/{RULE_ID}.md", render(_rule(PAGOS)))
    write(root, "ui/src/pagos.py", PAGOS)  # Same path in another source: not this rule's.
    commit_all(root)
    write(root, "ui/src/pagos.py", PAGOS.replace("> 100", "> 1"))
    assert check(Repository.at(root / "backend"), "main", include_local=True).findings == []
    write(root, "backend/src/pagos.py", PAGOS.replace("> 100", "> 1"))
    result = check(Repository.at(root / "backend"), "main", include_local=True)
    assert [f.level for f in result.findings] == [CHANGED]
    assert json.loads(to_json(result))["findings"][0]["path"] == "backend/src/pagos.py"


def test_a_broken_rule_file_is_an_error_not_a_pass(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repo, f"{RULES_DIR}/{RULE_ID}.md", "---\nid: x\n")
    os.chdir(repo)
    assert cli(["check", "--base", "main", "--include-local", "--format", "json"]) == 2
    assert "closing the header is missing" in capsys.readouterr().out


def test_the_command_exit_codes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = ["--root", str(repo), "--base", "main", "--include-local"]
    assert cli(["check", *root]) == 0
    write(repo, "src/pagos.py", PAGOS.replace("> 100", "> 150"))
    out = repo.parent / "check.sarif"
    assert cli(["check", *root, "--format", "sarif", "--output", str(out)]) == 1
    assert json.loads(out.read_text())["version"] == "2.1.0"
    assert cli(["check", "--root", str(repo), "--base", "no-existe"]) == 2
    assert "is not in this clone" in capsys.readouterr().err


def _run_hook(repo: Path) -> int:
    return subprocess.run(
        [str(hook_path(Repository.at(repo)))], cwd=repo, capture_output=True, check=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    ).returncode  # fmt: skip


def test_hooks_install_and_uninstall_leave_other_hooks_alone(repo: Path) -> None:
    repository = Repository.at(repo)
    command = [str(Path(os.environ.get("VIRTUAL_ENV", ".venv")) / "bin" / "scrinium")]
    if not Path(command[0]).exists():
        command = ["python", "-m", "scrinium"]
    path = hook_path(repository)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho propio\nexit 0\n")
    path.chmod(0o755)

    assert install(repository, command) == "added"
    assert install(repository, command) == "present"
    lines = path.read_text().splitlines()
    assert lines[0] == "#!/bin/sh" and lines[-2:] == ["echo propio", "exit 0"]
    assert _run_hook(repo) == 0
    write(repo, "src/pagos.py", PAGOS.replace("> 100", "> 150"))
    # The hook checks what the commit records: an unstaged change is not in it.
    assert _run_hook(repo) == 0
    git(repo, "add", "src/pagos.py")
    assert _run_hook(repo) == 1

    assert uninstall(repository) == "removed"
    assert path.read_text() == "#!/bin/sh\necho propio\nexit 0\n"
    assert uninstall(repository) == "absent"

    path.unlink()
    assert install(repository, command) == "created"
    assert uninstall(repository) == "deleted" and not path.exists()


def test_a_hook_in_another_language_is_not_edited(repo: Path) -> None:
    path = hook_path(Repository.at(repo))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env python3\nprint('hola')\n")
    from scrinium.application.check.hooks import HookError

    with pytest.raises(HookError, match="is not a shell script"):
        install(Repository.at(repo), ["scrinium"])
    assert path.read_text() == "#!/usr/bin/env python3\nprint('hola')\n"


def test_anchors_of_an_older_extractor_are_computed_again_from_the_base(repo: Path) -> None:
    old = render(_rule(PAGOS)).replace('decisions: "3"', 'decisions: "1"')
    old = old.replace('shape: "', 'shape: "00').replace('value: "', 'value: "00')
    write(repo, f"{RULES_DIR}/{RULE_ID}.md", old)
    commit_all(repo)
    git(repo, "branch", "-f", "main", "HEAD")
    write(repo, "src/pagos.py", PAGOS.replace("'ventas'", "'otras'"))
    assert _levels(repo, include_local=True) == []
    write(repo, "src/pagos.py", PAGOS.replace("> 100", "> 150"))
    result = _check(repo, include_local=True)
    assert [f.level for f in result.findings] == [CHANGED] and result.stale == 0


def test_staged_checks_the_index_not_the_working_folder(repo: Path) -> None:
    write(repo, "src/pagos.py", PAGOS.replace("> 100", "> 150"))
    git(repo, "add", "src/pagos.py")
    write(repo, "src/pagos.py", PAGOS)  # Undone in the folder, still staged.
    assert _levels(repo, include_local=True) == []
    staged = check(Repository.at(repo), "main", staged=True)
    assert [f.level for f in staged.findings] == [CHANGED] and staged.head == "staged"
