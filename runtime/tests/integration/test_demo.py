"""`scrinium demo` (phase 5.3): the example, its verified rules, a change and the warning."""

import subprocess
import sys
from pathlib import Path

from scrinium.application.check.engine import check
from scrinium.application.check.git import Repository
from scrinium.cli.demo import demo, prepare


def test_the_demo_breaks_a_rule_and_check_says_so(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    rules = prepare(repo)
    assert len(rules) == 3
    result = check(Repository.at(repo), "main")
    assert result.failed
    assert [finding.title for finding in result.findings] == [
        "Envío gratis desde 40 EUR en la península"
    ]
    # On main, nothing changed: the check passes.
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    assert not check(Repository.at(repo), "main").failed


def test_the_demo_is_the_same_on_every_machine(tmp_path: Path) -> None:
    heads = []
    for name in ("a", "b"):
        prepare(tmp_path / name)
        head = subprocess.run(
            ["git", "-C", str(tmp_path / name), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip
        heads.append(head)
    assert heads[0] == heads[1]


def test_the_demo_refuses_a_folder_in_use(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "algo.txt").write_text("mio")
    assert demo(tmp_path / "x") == 2
    assert (tmp_path / "x" / "algo.txt").read_text() == "mio"


def test_the_demo_command_runs_without_a_model(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scrinium.cli.main", "demo", "--dir", str(tmp_path / "d")],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "SCRINIUM_DATA_DIR": str(tmp_path / "data"),
             "HOME": str(tmp_path)},
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert "Cambia la regla" in result.stdout and "codigo 1" in result.stdout
    # It never touched the data directory.
    assert not (tmp_path / "data").exists()
