"""Marks the chunks that come from tests, so search and vectors can leave them out.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-11 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from scrinium.domain.paths import is_test_path

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "content_chunks",
        sa.Column("is_test", sa.Boolean(), server_default="0", nullable=False),
    )
    op.create_index("ix_content_chunks_is_test", "content_chunks", ["is_test"])
    # Existing chunks: the path of their revision decides, as it does for new ones.
    connection = op.get_bind()
    revisions = connection.execute(
        sa.text(
            "SELECT DISTINCT r.id, json_extract(r.metadata, '$.path') AS path "
            "FROM source_revisions AS r JOIN content_chunks AS c ON c.source_revision_id = r.id"
        )
    ).all()
    tests = [row.id for row in revisions if is_test_path(row.path or "")]
    for revision_id in tests:
        connection.execute(
            sa.text("UPDATE content_chunks SET is_test = 1 WHERE source_revision_id = :id"),
            {"id": revision_id},
        )


def downgrade() -> None:
    op.drop_index("ix_content_chunks_is_test", table_name="content_chunks")
    # Plain DROP COLUMN: recreating the table would drop its FTS5 triggers.
    op.execute("ALTER TABLE content_chunks DROP COLUMN is_test")
