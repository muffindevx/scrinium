"""Per project: mask personal data and long literals before a remote request (ADR-010).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-14 20:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN mask_personal BOOLEAN NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE projects ADD COLUMN mask_literals BOOLEAN NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE ai_requests ADD COLUMN masked INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    # Plain DROP COLUMN: recreating the table would need its foreign keys off.
    op.execute("ALTER TABLE ai_requests DROP COLUMN masked")
    op.execute("ALTER TABLE projects DROP COLUMN mask_literals")
    op.execute("ALTER TABLE projects DROP COLUMN mask_personal")
