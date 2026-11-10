"""SQL review as text. It never runs: not against Scrinium's SQLite or anything else.

sqlglot turns the text into a tree (AST) without connecting to any database. From the tree
come the tables read and written and the columns, compared with the provided schema and with
the rules that mention those tables. The SQL is not stored: the result only keeps table
and column names, and the position of a syntax error, not the text.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Final

import sqlglot
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError

from scrinium.application.analysis.release_comparison import SEVERITY_BY_CRITICALITY
from scrinium.application.analysis.runs import finish_run
from scrinium.application.knowledge.common import KnowledgeError
from scrinium.application.knowledge.entities import latest_version
from scrinium.application.knowledge.evidence import record_finding
from scrinium.domain.enums import AnalysisStatus, FindingType, Severity
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import (
    AnalysisRun,
    BusinessRule,
    BusinessRuleVersion,
    KnowledgeEntity,
)

MAX_SQL_CHARS: Final = 100_000
MAX_SCHEMA_CHARS: Final = 200_000
MAX_STATEMENTS: Final = 100
MAX_FINDINGS: Final = 50
# sqlglot names. "" is the generic dialect.
DIALECTS: Final = frozenset(
    {"", "postgres", "mysql", "sqlite", "tsql", "oracle", "bigquery", "snowflake", "duckdb"}
)

_WRITES: Final = {
    exp.Insert: "insert", exp.Update: "update", exp.Delete: "delete", exp.Merge: "merge",
}  # fmt: skip
_DDL: Final = {
    exp.Create: "create", exp.Drop: "drop", exp.Alter: "alter", exp.TruncateTable: "truncate",
}  # fmt: skip
_DESTRUCTIVE: Final = frozenset({"drop", "truncate"})


def _name(table: exp.Table) -> str:
    return ".".join(part for part in (table.db, table.name) if part).lower()


def parse_schema(text: str, dialect: str) -> dict[str, set[str]]:
    """Tables and columns of a schema in DDL (CREATE TABLE ...). The rest is ignored."""
    if len(text) > MAX_SCHEMA_CHARS:
        raise KnowledgeError("schema_too_large")
    try:
        statements = sqlglot.parse(text, read=dialect or None)
    except SqlglotError:
        raise KnowledgeError("schema_parse_error") from None
    schema: dict[str, set[str]] = {}
    for statement in statements:
        if isinstance(statement, exp.Create) and isinstance(statement.this, exp.Schema):
            table = statement.this.this
            if isinstance(table, exp.Table):
                schema[_name(table)] = {
                    col.name.lower()
                    for col in statement.this.expressions
                    if isinstance(col, exp.ColumnDef)
                }
    return schema


@dataclass
class Statement:
    index: int
    kind: str
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    columns: set[str] = field(default_factory=set)
    unknown_tables: set[str] = field(default_factory=set)
    unknown_columns: set[str] = field(default_factory=set)
    risks: list[str] = field(default_factory=list)


def _kind(node: exp.Expr) -> str:
    for kinds in (_WRITES, _DDL):
        for cls, name in kinds.items():
            if isinstance(node, cls):
                return name
    if isinstance(node, exp.Query):
        return "select"
    return "other"


def _target(node: exp.Expr) -> exp.Table | None:
    target = node.this
    if isinstance(target, exp.Schema):
        target = target.this
    return target if isinstance(target, exp.Table) else None


def describe(node: exp.Expr, index: int, schema: dict[str, set[str]]) -> Statement:
    kind = _kind(node)
    statement = Statement(index, kind)
    ctes = {cte.alias_or_name.lower() for cte in node.find_all(exp.CTE)}
    aliases: dict[str, str] = {}
    tables = []
    for table in node.find_all(exp.Table):
        name = _name(table)
        if not name or name in ctes:
            continue
        tables.append(name)
        aliases[table.alias_or_name.lower()] = name
    written: set[str] = set()
    if kind in ("insert", "update", "delete", "merge", "alter", "create"):
        target = _target(node)
        if target is not None:
            written.add(_name(target))
    elif kind in _DESTRUCTIVE:
        written.update(tables)
    statement.writes = written
    statement.reads = set(tables) - written
    for column in node.find_all(exp.Column):
        owner = aliases.get(column.table.lower()) if column.table else None
        if owner is None and not column.table and len(set(tables)) == 1:
            owner = tables[0]
        name = column.name.lower()
        statement.columns.add(f"{owner}.{name}" if owner else name)
        if schema and owner in schema and name not in schema[owner] and name != "*":
            statement.unknown_columns.add(f"{owner}.{name}")
    if schema and kind not in ("create",):
        statement.unknown_tables = {t for t in tables if t not in schema}
    if kind in ("update", "delete") and not node.args.get("where"):
        statement.risks.append("write_without_where")
    if kind in _DESTRUCTIVE:
        statement.risks.append("destructive_ddl")
    if any(isinstance(star, exp.Star) for star in node.find_all(exp.Star)) and kind == "select":
        statement.risks.append("select_star")
    return statement


def related_rules(
    session: Session, project_id: uuid.UUID, tables: set[str]
) -> dict[str, list[tuple[BusinessRule, KnowledgeEntity, BusinessRuleVersion, str]]]:
    """Rules that name each table: in their scope, their targets or their statement."""
    found: dict[str, list[tuple[BusinessRule, KnowledgeEntity, BusinessRuleVersion, str]]] = {}
    rows = session.execute(
        select(BusinessRule, KnowledgeEntity)
        .join(KnowledgeEntity, KnowledgeEntity.id == BusinessRule.knowledge_entity_id)
        .where(BusinessRule.project_id == project_id)
    ).all()
    for rule, entity in rows:
        version = latest_version(session, entity.id)
        content = session.get(BusinessRuleVersion, version.id) if version is not None else None
        if content is None:
            continue
        declared = {item.lower() for item in content.scope.get("entities", [])}
        declared |= {
            t["ref"].lower() for t in content.impact_targets if t.get("kind") == "data_entity"
        }
        text = f"{content.title} {content.statement}"
        for table in tables:
            short = table.rsplit(".", 1)[-1]
            if table in declared or short in declared:
                match = "declared"
            elif re.search(rf"\b{re.escape(short)}\b", text, re.IGNORECASE):
                match = "mentioned"
            else:
                continue
            found.setdefault(table, []).append((rule, entity, content, match))
    return found


def review_sql(
    database: Database, run_id: uuid.UUID, sql: str, dialect: str, schema_text: str | None
) -> None:
    limitations = [
        "El SQL no se ha ejecutado. Sin datos reales no se sabe cuántas filas afectaría.",
    ]
    try:
        schema = parse_schema(schema_text, dialect) if schema_text else {}
    except KnowledgeError as error:
        finish_run(database, run_id, AnalysisStatus.FAILED, {"limitations": []}, error.code)
        return
    if not schema:
        limitations.append("Sin esquema: no se comprueban tablas ni columnas desconocidas.")
    try:
        nodes = [n for n in sqlglot.parse(sql, read=dialect or None) if n is not None]
    except ParseError as error:
        detail = error.errors[0] if error.errors else {}
        # Position only: the sqlglot message copies pieces of the SQL.
        position = {"line": detail.get("line"), "col": detail.get("col")}
        finish_run(
            database, run_id, AnalysisStatus.FAILED, {"parse_error": position}, "sql_parse_error"
        )
        return
    except SqlglotError:
        finish_run(database, run_id, AnalysisStatus.FAILED, {"parse_error": {}}, "sql_parse_error")
        return
    if not nodes:
        finish_run(database, run_id, AnalysisStatus.FAILED, {}, "sql_empty")
        return
    if len(nodes) > MAX_STATEMENTS:
        limitations.append(f"Solo se analizan las primeras {MAX_STATEMENTS} sentencias.")
    statements = [describe(node, i, schema) for i, node in enumerate(nodes[:MAX_STATEMENTS], 1)]
    written = {t for s in statements for t in s.writes}
    touched = written | {t for s in statements for t in s.reads}

    with database.session() as session:
        run = session.get_one(AnalysisRun, run_id)
        rules = related_rules(session, run.project_id, touched)
        findings = 0

        def finding(**kwargs: Any) -> None:
            nonlocal findings
            if findings < MAX_FINDINGS:
                record_finding(session, run, **kwargs)
                findings += 1

        for statement in statements:
            where = {"statement": statement.index}
            if "write_without_where" in statement.risks:
                finding(
                    finding_type=FindingType.IMPACT_RISK, severity=Severity.HIGH,
                    title=f"{statement.kind.upper()} sin WHERE en la sentencia {statement.index}",
                    description="Sin condición, la sentencia afecta a todas las filas de "
                    f"{', '.join(sorted(statement.writes))}.", location=where,
                    next_step="Confirma que se quiere cambiar la tabla entera.",
                )  # fmt: skip
            if "destructive_ddl" in statement.risks:
                finding(
                    finding_type=FindingType.IMPACT_RISK, severity=Severity.HIGH,
                    title=f"{statement.kind.upper()} en la sentencia {statement.index}",
                    description="Borra la estructura o todos los datos de "
                    f"{', '.join(sorted(statement.writes))}.", location=where,
                    next_step="Comprueba que hay copia y que nada depende de esa tabla.",
                )  # fmt: skip
            unknown = sorted(statement.unknown_tables | statement.unknown_columns)
            if unknown:
                finding(
                    finding_type=FindingType.KNOWLEDGE_GAP, severity=Severity.LOW,
                    title=f"Nombres que no están en el esquema (sentencia {statement.index})",
                    description=", ".join(unknown)[:2000], location=where,
                    next_step="Revisa si el esquema aportado está completo o hay un error.",
                )  # fmt: skip
        for table in sorted(written):
            for rule, entity, content, match in rules.get(table, []):
                finding(
                    finding_type=FindingType.IMPACT_RISK,
                    severity=SEVERITY_BY_CRITICALITY[rule.criticality],
                    title=f"Escribe en {table}, que afecta a {entity.name}"[:300],
                    description=f"La regla dice: {content.statement}"[:4000],
                    location={"table": table, "entity_id": str(entity.id), "match": match},
                    rule_version_id=content.knowledge_entity_version_id,
                    next_step="Falta evidencia: compara la escritura con el comportamiento "
                    "esperado de la regla y con sus tests.",
                )  # fmt: skip
        result = {
            "dialect": dialect or "generic",
            "schema_tables": len(schema),
            "statements": [
                {"index": s.index, "kind": s.kind, "reads": sorted(s.reads),
                 "writes": sorted(s.writes), "columns": sorted(s.columns)[:200],
                 "unknown_tables": sorted(s.unknown_tables),
                 "unknown_columns": sorted(s.unknown_columns), "risks": s.risks}
                for s in statements
            ],
            "related_rules": {
                table: [{"entity_id": str(e.id), "name": e.name, "match": m}
                        for _, e, _, m in items]
                for table, items in rules.items()
            },
            "findings": findings,
            "limitations": limitations,
            "executed": False,
        }  # fmt: skip
    finish_run(database, run_id, AnalysisStatus.COMPLETED, result)
