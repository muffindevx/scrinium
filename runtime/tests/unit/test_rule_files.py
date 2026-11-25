"""Rule files (.scrinium/rules/): deterministic writing and strict reading (phase 2)."""

from pathlib import Path

import pytest

from scrinium.application.check.rule_files import (
    FileAnchor,
    RuleFile,
    RuleFileError,
    load,
    parse,
    render,
    write_all,
)

RULE = RuleFile(
    id="0193a5c2-0000-7000-8000-000000000001",
    title='Comisión "grande"',
    statement="Las ventas de más de 100 pagan un 2 %.\nSalvo las internas.",
    verified_by="Ana",
    verified_at="2026-11-16T18:00:00Z",
    anchors=(
        FileAnchor("src/b.py", None, "table", 5, 9, "ab12", "cd34"),
        FileAnchor("src/a.py", "Pedido.borrar", "guard", 3, 4, "ef56", "0789", sketch="1e5"),
    ),
)


def test_rendering_is_stable_and_round_trips() -> None:
    text = render(RULE)
    again = parse(text)
    assert render(again) == text
    # Anchors in path order, whatever order they came in.
    assert again.anchors[0].path == "src/a.py" and again.anchors[0].symbol == "Pedido.borrar"
    assert again.anchors[1].symbol is None and again.anchors[1].sketch == ""
    assert again.title == 'Comisión "grande"' and again.statement == RULE.statement
    assert text.endswith("\n") and "\r" not in text


def test_writing_twice_touches_nothing(tmp_path: Path) -> None:
    assert write_all(tmp_path, [RULE]) == (1, 0, 0)
    assert write_all(tmp_path, [RULE]) == (0, 1, 0)
    (tmp_path / "README.md").write_text("notas")
    assert write_all(tmp_path, []) == (0, 0, 1)
    assert [p.name for p in tmp_path.iterdir()] == ["README.md"]


def test_a_rule_file_must_be_named_by_its_id(tmp_path: Path) -> None:
    (tmp_path / "0193a5c2-0000-7000-8000-000000000002.md").write_text(render(RULE))
    with pytest.raises(RuleFileError, match="must be named"):
        load(tmp_path)


@pytest.mark.parametrize(
    ("change", "line", "message"),
    [
        (("scrinium: 1", "scrinium: 2"), 2, "format version"),
        (("lines: [3, 4]", "lines: [4, 3]"), 12, "out of range"),
        (('shape: "ef56"', 'shape: "no-hex"'), 13, "fingerprint"),
        (("title: ", "title: [x] "), 4, "no admitido"),
    ],
)
def test_errors_say_where(change: tuple[str, str], line: int, message: str) -> None:
    text = render(RULE).replace(*change, 1)
    with pytest.raises(RuleFileError, match=message) as error:
        parse(text, "r.md")
    assert error.value.line == line
