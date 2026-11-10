"""Sources per project, current revision of each file, and excluded-file counter.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07 11:19:55.086111
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ADD COLUMN instead of batch_alter_table: recreating `source_connections` requires dropping
# the original table, and with foreign keys on that fails as soon as it has children. Alembic
# does not generate ADD COLUMN with REFERENCES for SQLite, though SQLite allows it: raw SQL.


def upgrade() -> None:
    op.execute(
        "ALTER TABLE source_connections ADD COLUMN project_id CHAR(32) "
        "CONSTRAINT fk_source_connections_project_id_projects REFERENCES projects (id)"
    )
    op.create_index("ix_source_connections_project_id", "source_connections", ["project_id"])
    op.execute(
        "ALTER TABLE source_items ADD COLUMN current_revision_id CHAR(32) "
        "CONSTRAINT fk_source_items_current_revision_id_source_revisions "
        "REFERENCES source_revisions (id)"
    )
    op.add_column(
        "sync_runs",
        sa.Column("items_excluded", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sync_runs", "items_excluded")
    with op.batch_alter_table("source_items") as batch:
        batch.drop_column("current_revision_id")
    op.drop_index("ix_source_connections_project_id", "source_connections")
    # SQLite cannot drop a column with a foreign key: the table must be recreated. Development only.
    with op.batch_alter_table("source_connections") as batch:
        batch.drop_column("project_id")
