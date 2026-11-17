"""An index on each file's current revision (phase 6.2).

The ⌘K palette finds functions by name and then their file. Without this index SQLite read
every file first, and a search over 100 000 fragments took over 100 ms.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-17 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_source_items_current_revision_id", "source_items", ["current_revision_id"])


def downgrade() -> None:
    op.drop_index("ix_source_items_current_revision_id", "source_items")
