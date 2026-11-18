"""What `scrinium check` found, as JSON, SARIF 2.1.0 or Markdown (phase 2.2).

SARIF is what code hosts read to annotate a pull request: one `rule` per verified rule touched
(its `id` is the rule's id), one `result` per anchor, with its place in the repository (paths
from the top level, so a source in a subfolder points to the right file).
"""

import json
from typing import Any, Final

from scrinium import __version__
from scrinium.application.check.engine import CHANGED, REMOVED, REVIEW, CheckResult, Finding
from scrinium.application.check.tickets import TicketRef

SARIF_SCHEMA: Final = "https://json.schemastore.org/sarif-2.1.0.json"
_KINDS: Final = {"error": "fail", "warning": "review", "note": "informational"}
_LEVEL_TITLES: Final = {
    "changed": "Cambia la regla",
    "review": "Revisar",
    "removed": "Retirada",
    "updated": "Actualizada en este cambio",
    "moved": "Movida",
}


def _where(result: CheckResult, finding: Finding) -> tuple[str, int, int]:
    """The place to point at: where the decision is now, or where it was."""
    if finding.now is not None:
        path, start, end, _ = finding.now
    else:
        path, start, end = finding.anchor.path, finding.anchor.start_line, finding.anchor.end_line
    return f"{result.prefix}{path}", start, end


def to_json(result: CheckResult) -> str:
    payload: dict[str, Any] = {
        "tool": f"scrinium {__version__}",
        "base": result.base,
        "head": result.head,
        "failed": result.failed,
        "changed_files": result.changed_files,
        "rules": result.rules,
        "anchors_checked": result.anchors_checked,
        "stale_anchors": result.stale,
        "seconds": round(result.seconds, 3),
        "errors": result.errors,
        "require_ticket": result.require_ticket,
        "branch": result.branch,
        "without_ticket": [f.rule_id for f in result.without_ticket],
        "findings": [
            {
                "rule_id": f.rule_id,
                "title": f.title,
                "level": f.level,
                "severity": f.severity,
                "message": f.message,
                "tickets": [_ticket(ref) for ref in f.tickets],
                "path": _where(result, f)[0],
                "start_line": _where(result, f)[1],
                "end_line": _where(result, f)[2],
                "anchor": {
                    "path": f"{result.prefix}{f.anchor.path}",
                    "start_line": f.anchor.start_line,
                    "end_line": f.anchor.end_line,
                    "symbol": f.anchor.symbol,
                    "kind": f.anchor.kind,
                },
                **(
                    {"explanation": result.explanations[f.key]}
                    if f.key in result.explanations
                    else {}
                ),
            }
            for f in result.findings
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _ticket(ref: TicketRef) -> dict[str, str]:
    return {"key": ref.key, "origin": ref.origin, "where": ref.where}


def _ticket_note(finding: Finding) -> str:
    if finding.tickets:
        return "Tickets: " + ", ".join(ref.key for ref in finding.tickets) + "."
    if finding.needs_ticket:
        return "Ningún commit ni la rama nombran un ticket para este cambio de regla."
    return ""


def to_sarif(result: CheckResult) -> str:
    rules: dict[str, dict[str, Any]] = {}
    results = []
    for finding in result.findings:
        rules.setdefault(
            finding.rule_id,
            {
                "id": finding.rule_id,
                "name": "VerifiedBusinessRule",
                "shortDescription": {"text": finding.title[:200]},
                "properties": {"tags": ["business-rule", "scrinium"]},
            },
        )
        path, start, end = _where(result, finding)
        message = finding.message
        note = _ticket_note(finding)
        if note:
            message += f" {note}"
        explanation = result.explanations.get(finding.key)
        if explanation:
            message += f"\n\n{explanation}"
        results.append(
            {
                "ruleId": finding.rule_id,
                "level": finding.severity,
                "kind": _KINDS[finding.severity],
                "message": {"text": message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": path, "uriBaseId": "%SRCROOT%"},
                            "region": {"startLine": start, "endLine": end},
                        }
                    }
                ],
                "partialFingerprints": {
                    "scriniumAnchor/v1": f"{finding.rule_id}:{finding.anchor.shape}",
                },
                "properties": {
                    "scriniumLevel": finding.level,
                    "tickets": [ref.key for ref in finding.tickets],
                },
            }
        )  # fmt: skip
    sarif = {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scrinium",
                        "version": __version__,
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
                "invocations": [
                    {
                        "executionSuccessful": not result.errors,
                        "toolExecutionNotifications": [
                            {"level": "error", "message": {"text": error}}
                            for error in result.errors
                        ],
                    }
                ],
            }
        ],
    }
    return json.dumps(sarif, ensure_ascii=False, indent=2) + "\n"


def _count(number: int, one: str, many: str) -> str:
    return f"{number} {one if number == 1 else many}"


def to_markdown(result: CheckResult) -> str:
    lines = ["## Scrinium: reglas verificadas", ""]
    failing = [f for f in result.findings if f.level == CHANGED]
    warnings = [f for f in result.findings if f.level in {REVIEW, REMOVED}]
    if result.errors:
        lines.append("**Error al leer las reglas:**")
        lines += [f"- `{error}`" for error in result.errors]
        lines.append("")
    if not result.findings:
        lines.append(
            f"Ninguna regla verificada cambia ({_count(result.rules, 'regla', 'reglas')}, "
            f"{_count(result.changed_files, 'archivo cambiado', 'archivos cambiados')})."
        )
    else:
        verdict = "falla" if result.failed else "pasa"
        lines.append(
            f"El check **{verdict}**: {_count(len(failing), 'regla cambia', 'reglas cambian')}, "
            f"{len(warnings)} para revisar ({_count(result.rules, 'regla', 'reglas')}, "
            f"{_count(result.changed_files, 'archivo cambiado', 'archivos cambiados')})."
        )
        lines += ["", "| Nivel | Regla | Dónde | Tickets |", "|---|---|---|---|"]
        for finding in result.findings:
            path, start, end = _where(result, finding)
            title = finding.title.replace("|", "\\|")
            keys = ", ".join(ref.key for ref in finding.tickets)
            if not keys:
                keys = "**sin ticket**" if finding.needs_ticket else "—"
            lines.append(
                f"| {_LEVEL_TITLES[finding.level]} | {title} | `{path}:{start}-{end}` | {keys} |"
            )
        missing = result.without_ticket
        if missing:
            verb = "falla por eso" if result.require_ticket else "aviso"
            lines += [
                "",
                f"**{_count(len(missing), 'regla cambia', 'reglas cambian')} sin ticket** "
                f"({verb}): ningún commit del cambio que toca su archivo ni la rama "
                f"`{result.branch or '—'}` nombra uno.",
            ]
        explained = [
            (f, result.explanations[f.key]) for f in result.findings if f.key in result.explanations
        ]
        if explained:
            lines += ["", "**Explicación (modelo, solo orientativa):**", ""]
            lines += [f"- *{f.title}*: {text}" for f, text in explained]
    if result.stale:
        lines += [
            "",
            f"{_count(result.stale, 'ancla antigua no se pudo', 'anclas antiguas no se pudieron')} "
            "recalcular: exporta las reglas de nuevo (`scrinium rules export`).",
        ]
    lines += [
        "",
        f"<sub>scrinium {__version__} · base `{result.base}` · {result.seconds:.2f} s</sub>",
    ]
    return "\n".join(lines) + "\n"


FORMATS: Final = {"json": to_json, "sarif": to_sarif, "markdown": to_markdown}
