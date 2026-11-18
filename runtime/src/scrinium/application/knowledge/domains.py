"""Business domains for grouping coverage: proposed from folders, editable in the repository.

Scrinium has no notion of a domain of its own (payments, shipping, memberships…). So:

- **Proposed:** a file's domain is its first meaningful folder, skipping the generic ones that
  only organise code (`src/`, `packages/`, `apps/`, `lib/`, `main/java/com/`…). So
  `packages/db/schema.sql` is `db` and `addons/sale/models/order.py` is `sale`. No model.
- **Declared:** `.scrinium/domains.yaml` in a source names domains by path prefix, in the same
  strict format as the rule files (ADR-012), so it travels with the repository and is reviewed
  in pull requests::

      scrinium: 1
      domains:
        - name: "Pagos"
          path: "src/billing/"
        - name: "Pagos"
          path: "src/payments/"

  Entries with the same name join. The longest matching prefix wins; files no entry covers keep
  their proposed domain.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from scrinium.application.check.yaml_subset import (
    FileFormatError,
    items,
    optional,
    parse,
    text_of,
)

DOMAINS_FILE: Final = Path(".scrinium") / "domains.yaml"
ROOT_DOMAIN: Final = ""
MAX_ENTRIES: Final = 500
# Folders that organise code without saying what it is about.
_GENERIC: Final = frozenset(
    {"src", "source", "sources", "lib", "libs", "app", "apps", "packages", "pkg", "pkgs",
     "modules", "addons", "internal", "code", "server", "client", "backend", "frontend",
     "services", "service", "main", "java", "kotlin", "scala", "python", "go", "js", "ts",
     "com", "org", "net", "io", "core", "common", "shared", "cmd"}
)  # fmt: skip


def proposed_domain(path: str) -> str:
    """The first meaningful folder of `path`; ROOT_DOMAIN for files at the top."""
    folders = path.split("/")[:-1]
    for folder in folders:
        if folder and not folder.startswith(".") and folder.lower() not in _GENERIC:
            return folder
    # Only generic folders (`src/app.py`, `apps/api/x.ts`): the last one is the best name.
    return folders[-1] if folders else ROOT_DOMAIN


@dataclass
class Domains:
    # (prefix, name), longest prefix first.
    declared: list[tuple[str, str]] = field(default_factory=list)
    error: str | None = None

    def of(self, path: str) -> tuple[str, bool]:
        """The domain of `path` and whether a person declared it."""
        for prefix, name in self.declared:
            if path == prefix.rstrip("/") or path.startswith(prefix if prefix.endswith("/")
                                                              else f"{prefix}/"):  # fmt: skip
                return name, True
        return proposed_domain(path), False


def load_domains(root: Path) -> Domains:
    """The declared domains of the source at `root`; empty if there is no file. A broken file
    is reported, never fatal: coverage falls back to the proposed domains."""
    path = root / DOMAINS_FILE
    if not path.is_file():
        return Domains()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        entries = parse(lines, DOMAINS_FILE)
        if optional(entries, "scrinium") != "1":
            raise FileFormatError(DOMAINS_FILE, 1, "falta «scrinium: 1»")
        declared = []
        for fields in items(entries, "domains", DOMAINS_FILE)[:MAX_ENTRIES]:
            at = min(number for number, _ in fields.values())
            name = text_of(optional(fields, "name"), DOMAINS_FILE, at, "name").strip()
            prefix = text_of(optional(fields, "path"), DOMAINS_FILE, at, "path").strip()
            prefix = prefix.removeprefix("./")
            if not name or not prefix or prefix.startswith("/") or ".." in prefix.split("/"):
                raise FileFormatError(DOMAINS_FILE, at, "ruta o nombre no válidos")
            declared.append((prefix, name[:100]))
    except FileFormatError as error:
        return Domains(error=str(error))
    except OSError:
        return Domains(error=f"{DOMAINS_FILE}: no se pudo leer")
    declared.sort(key=lambda entry: -len(entry[0]))
    return Domains(declared)
