"""Why a relationship between rules was derived from the code (stage 3, phase C).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-12 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("entity_relationships", sa.Column("basis", sa.String(length=200), nullable=True))


def downgrade() -> None:
    # Plain DROP COLUMN: recreating the table would need its foreign keys off.
    op.execute("ALTER TABLE entity_relationships DROP COLUMN basis")
