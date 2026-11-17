"""How fast the ⌘K palette answers (phase 6.2): target under 100 ms at p95 with 100 000 fragments.

Builds a throwaway database with one source of N files, each with its revision, chunks and
functions (100 000 fragments by default), and a few thousand rules with tickets, then times
`palette` for typical queries: short and long, prefixes, and text that matches nothing.

    uv run python benchmarks/palette_speed.py --fragments 100000
"""

import argparse
import hashlib
import json
import statistics
import tempfile
import time
import uuid
from pathlib import Path

from sqlalchemy import insert

from scrinium.application.retrieval.palette import palette
from scrinium.application.workspaces import ensure_local_workspace
from scrinium.domain.enums import EntityType, ItemLifecycle
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import (
    CodeSymbol,
    ContentChunk,
    KnowledgeEntity,
    Project,
    SourceConnection,
    SourceItem,
    SourceRevision,
)

_WORDS = ("pedido", "factura", "envio", "descuento", "cliente", "stock", "pago", "tarifa")
_QUERIES = ("pe", "fac", "descuento", "calcular_envio", "Cliente", "xyz", "pago_tarifa", "TCK-1")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def seed(database: Database, fragments: int, rules: int) -> uuid.UUID:
    per_file = 10
    files = fragments // per_file
    with database.session() as session:
        workspace = ensure_local_workspace(session)
        project = Project(workspace_id=workspace.id, name="Bench", project_key="bench")
        session.add(project)
        session.flush()
        source = SourceConnection(
            workspace_id=workspace.id, project_id=project.id, provider="local_git",
            display_name="bench", configuration={"root": "/bench"},
        )  # fmt: skip
        session.add(source)
        session.flush()
        items, revisions, chunks, symbols = [], [], [], []
        for number in range(files):
            word = _WORDS[number % len(_WORDS)]
            path = f"src/{word}/modulo_{number}.py"
            item_id, revision_id = uuid.uuid4(), uuid.uuid4()
            items.append({
                "id": item_id, "workspace_id": workspace.id, "source_connection_id": source.id,
                "provider_item_id": path, "item_type": "file", "canonical_uri": path,
                "title": path, "lifecycle_status": ItemLifecycle.ACTIVE,
                "access_metadata": {},
            })  # fmt: skip
            revisions.append({
                "id": revision_id, "workspace_id": workspace.id, "source_item_id": item_id,
                "revision_key": path, "content_hash": _hash(path), "metadata_": {"path": path},
                "parser_name": "bench", "parser_version": "1",
                "text_content": "",
            })  # fmt: skip
            for part in range(per_file):
                name = f"calcular_{word}_{number}_{part}"
                chunks.append({
                    "id": uuid.uuid4(), "workspace_id": workspace.id,
                    "source_revision_id": revision_id, "chunk_index": part,
                    "text_content": f"def {name}(): pass", "locator": {"start_line": part * 10 + 1,
                    "end_line": part * 10 + 9}, "content_hash": _hash(name), "token_count": 5,
                })  # fmt: skip
                symbols.append({
                    "id": uuid.uuid4(), "workspace_id": workspace.id,
                    "source_revision_id": revision_id, "language": "python", "kind": "function",
                    "name": name, "qualified_name": name, "start_line": part * 10 + 1,
                    "end_line": part * 10 + 9, "content_hash": _hash(name),
                    "logic_hash": _hash(name), "structure_version": "1",
                })  # fmt: skip
        session.execute(insert(SourceItem), items)
        session.execute(insert(SourceRevision), revisions)
        session.execute(
            SourceItem.__table__.update()
            .where(SourceItem.__table__.c.id == SourceRevision.__table__.c.source_item_id)
            .values(current_revision_id=SourceRevision.__table__.c.id)
        )
        session.execute(insert(ContentChunk), chunks)
        session.execute(insert(CodeSymbol), symbols)
        session.execute(
            insert(KnowledgeEntity),
            [
                {
                    "id": uuid.uuid4(),
                    "workspace_id": workspace.id,
                    "project_id": project.id,
                    "entity_type": EntityType.BUSINESS_RULE,
                    "canonical_key": f"bench/{n}",
                    "name": f"Regla de {_WORDS[n % len(_WORDS)]} {n}",
                    "summary": "",
                    "metadata_": {"tickets": [f"TCK-{n}"]},
                }
                for n in range(rules)
            ],
        )
        return project.id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fragments", type=int, default=100_000)
    parser.add_argument("--rules", type=int, default=5_000)
    parser.add_argument("--rounds", type=int, default=20)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="scrinium-palette-") as tmp:
        database = Database.open(Path(tmp))
        project_id = seed(database, args.fragments, args.rules)
        timings = []
        for _ in range(args.rounds):
            for query in _QUERIES:
                with database.session() as session:
                    started = time.perf_counter()
                    palette(session, project_id, query)
                    timings.append((time.perf_counter() - started) * 1000)
        database.dispose()
    timings.sort()
    print(json.dumps({
        "fragments": args.fragments, "rules": args.rules, "queries": len(timings),
        "p50_ms": round(statistics.median(timings), 1),
        "p95_ms": round(timings[int(len(timings) * 0.95) - 1], 1),
        "max_ms": round(timings[-1], 1),
    }, indent=2))  # fmt: skip


if __name__ == "__main__":
    main()
