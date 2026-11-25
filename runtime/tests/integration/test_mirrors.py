"""Product manifest and mirrors between repositories (phase 3.4 and 3.5)."""

import uuid
from pathlib import Path

import pytest

from scrinium.application.check.product import MANIFEST, compare_mirrors, load_manifest
from scrinium.application.check.rule_files import RULES_DIR, FileAnchor, RuleFile, write_all
from scrinium.application.check.yaml_subset import FileFormatError
from scrinium.cli.rules import main as cli
from scrinium.infrastructure.decisions import parse_decisions

BACKEND = """\
DESCUENTO_MAXIMO = 0.25
PEDIDO_MINIMO = 30


def descuento(porcentaje):
    if porcentaje > DESCUENTO_MAXIMO:
        raise ValueError("demasiado")
    return porcentaje


def cupon(subtotal):
    if subtotal < PEDIDO_MINIMO:
        raise ValueError("pedido pequeño")
    return True


def borrable(estado):
    if estado not in ("borrador", "cancelado"):
        raise ValueError("no se borra")
    return True
"""
FRONTEND = """\
export function descuento(porcentaje: number) {
  if (porcentaje > 0.25) {
    throw new Error("demasiado")
  }
  return porcentaje
}

export function cupon(subtotal: number) {
  if (subtotal < 30) {
    throw new Error("pequeño")
  }
  return true
}

export function borrable(estado: string) {
  if (!["borrador", "cancelado"].includes(estado)) {
    throw new Error("no")
  }
  return true
}
"""


def _rule(folder: Path, path: str, function: str, title: str) -> RuleFile:
    text = (folder / path).read_text()
    guard = next(
        d for d in parse_decisions(path, text) if d.kind == "guard" and d.symbol == function
    )
    anchor = FileAnchor(
        path, guard.symbol, guard.kind, guard.start_line, guard.end_line, guard.shape,
        guard.value, guard.sketch,
    )  # fmt: skip
    return RuleFile(str(uuid.uuid4()), title, title, verified_by="Ana", anchors=(anchor,))


def _product(root: Path, name: str, frontend: str = FRONTEND) -> dict[str, tuple[str, str]]:
    """A backend and a UI side by side, a verified rule per function on each, and the manifest
    pairing them. Returns function name to (backend id, UI id)."""
    backend, ui = root / name / "backend", root / name / "ui"
    (backend / "tienda").mkdir(parents=True)
    (ui / "src").mkdir(parents=True)
    (backend / "tienda/reglas.py").write_text(BACKEND)
    (ui / "src/reglas.ts").write_text(frontend)
    pairs = {}
    backend_rules, ui_rules = [], []
    for function in ("descuento", "cupon", "borrable"):
        first = _rule(backend, "tienda/reglas.py", function, f"{function} (API)")
        second = _rule(ui, "src/reglas.ts", function, f"{function} (UI)")
        backend_rules.append(first)
        ui_rules.append(second)
        pairs[function] = (first.id, second.id)
    write_all(backend / RULES_DIR, backend_rules)
    write_all(ui / RULES_DIR, ui_rules)
    mirrors = "".join(
        f'  - rule: "{a}"\n    mirror: "{b}"\n' for a, b in pairs.values()
    )  # fmt: skip
    (backend / MANIFEST).write_text(
        f'scrinium: 1\nproduct: "{name}"\nsources:\n  - name: "backend"\n    path: "."\n'
        f'  - name: "ui"\n    path: "../ui"\nmirrors:\n{mirrors}'
    )
    return pairs


def test_mirrors_that_agree_do_not_diverge(tmp_path: Path) -> None:
    _product(tmp_path, "tienda")
    manifest = load_manifest(tmp_path / "tienda/backend")
    assert manifest is not None and [s.name for s in manifest.sources] == ["backend", "ui"]
    report = compare_mirrors(manifest)
    assert report.pairs == 3 and report.divergent == [] and report.errors == []


def test_every_seeded_divergence_is_found(tmp_path: Path) -> None:
    seeded = FRONTEND.replace("> 0.25", "> 0.30").replace(
        '["borrador", "cancelado"]', '["borrador"]'
    )
    _product(tmp_path, "tienda", seeded)
    report = compare_mirrors(load_manifest(tmp_path / "tienda/backend"))  # type: ignore[arg-type]
    found = sorted(first.rule.title or "" for first, _ in report.divergent)
    assert found == ["borrable (API)", "descuento (API)"]
    by_title = {first.rule.title: (first, second) for first, second in report.divergent}
    first, second = by_title["descuento (API)"]
    assert first.values == ("0.25",) and second.values == ("0.3",)


def test_three_products_never_cross(tmp_path: Path) -> None:
    ids = {name: _product(tmp_path, name) for name in ("tienda", "envios", "stock")}
    for name in ids:
        report = compare_mirrors(load_manifest(tmp_path / name / "backend"))  # type: ignore[arg-type]
        assert report.pairs == 3 and report.errors == [] and report.divergent == []
    # A mirror to another product's rule is an error, never a link.
    manifest = tmp_path / "tienda/backend" / MANIFEST
    stranger = ids["envios"]["cupon"][1]
    manifest.write_text(
        manifest.read_text()
        + f'  - rule: "{ids["tienda"]["cupon"][0]}"\n    mirror: "{stranger}"\n'
    )
    report = compare_mirrors(load_manifest(tmp_path / "tienda/backend"))  # type: ignore[arg-type]
    assert report.pairs == 3 and len(report.errors) == 1 and stranger in report.errors[0]


def test_the_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _product(tmp_path, "tienda", FRONTEND.replace("< 30", "< 50"))
    assert cli(["mirrors", "--root", str(tmp_path / "tienda/backend")]) == 1
    assert "50 in ui" in capsys.readouterr().out
    assert cli(["mirrors", "--root", str(tmp_path)]) == 2


def test_a_broken_manifest_says_where(tmp_path: Path) -> None:
    (tmp_path / MANIFEST).write_text('scrinium: 1\nproduct: "x"\nsources: {a: 1}\n')
    with pytest.raises(FileFormatError) as error:
        load_manifest(tmp_path)
    assert error.value.line == 3
