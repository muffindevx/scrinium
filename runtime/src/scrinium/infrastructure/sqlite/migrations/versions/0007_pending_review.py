"""Pending-review flag on knowledge entities.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-07 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_entities", sa.Column("review_reason", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_entities") as batch:
        batch.drop_column("review_reason")
