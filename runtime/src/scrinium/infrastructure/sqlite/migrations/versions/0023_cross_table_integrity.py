"""Integrity between tables, checked by the database too (R5).

The service already keeps these; a bug, a script or a restore that went around it must not:

- a citation (`evidence_records`) cites a revision of a source of its own project (a remote
  source with no project may serve several);
- a file's current revision (`source_items.current_revision_id`) is a revision of that file;
- a call edge (`code_edges`) joins symbols of current or past revisions of its own project.

Only new rows and changes are checked: rows written before this migration are left as they are.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-17 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVIDENCE_PROJECT = """
    SELECT RAISE(ABORT, 'evidence_records: la revision citada es de otro proyecto')
    WHERE new.source_revision_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM source_revisions r
        JOIN source_items i ON i.id = r.source_item_id
        JOIN source_connections c ON c.id = i.source_connection_id
        WHERE r.id = new.source_revision_id
          AND c.project_id IS NOT NULL AND c.project_id <> new.project_id
    );
"""

_CURRENT_REVISION = """
    SELECT RAISE(ABORT, 'source_items: la revision actual es de otro archivo')
    WHERE new.current_revision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM source_revisions r
        WHERE r.id = new.current_revision_id AND r.source_item_id = new.id
    );
"""

_EDGE_PROJECT = """
    SELECT RAISE(ABORT, 'code_edges: un simbolo es de otro proyecto')
    WHERE EXISTS (
        SELECT 1 FROM code_symbols s
        JOIN source_revisions r ON r.id = s.source_revision_id
        JOIN source_items i ON i.id = r.source_item_id
        JOIN source_connections c ON c.id = i.source_connection_id
        WHERE s.id IN (new.from_symbol_id, new.to_symbol_id)
          AND c.project_id IS NOT NULL AND c.project_id <> new.project_id
    );
"""

_TRIGGERS = {
    "evidence_records_same_project": ("evidence_records", "source_revision_id, project_id",
                                      _EVIDENCE_PROJECT),
    "source_items_current_revision": ("source_items", "current_revision_id", _CURRENT_REVISION),
    "code_edges_same_project": ("code_edges", "from_symbol_id, to_symbol_id, project_id",
                                _EDGE_PROJECT),
}  # fmt: skip


def upgrade() -> None:
    for name, (table, columns, body) in _TRIGGERS.items():
        op.execute(f"CREATE TRIGGER {name}_insert BEFORE INSERT ON {table} BEGIN {body} END")
        op.execute(
            f"CREATE TRIGGER {name}_update BEFORE UPDATE OF {columns} ON {table} BEGIN {body} END"
        )


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}_insert")
        op.execute(f"DROP TRIGGER IF EXISTS {name}_update")
