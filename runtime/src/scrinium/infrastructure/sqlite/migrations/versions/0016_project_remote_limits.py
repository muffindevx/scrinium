"""Per project: paths never sent to a remote model, and a monthly token budget (ADR-010).

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-14 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN never_send JSON NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE projects ADD COLUMN remote_token_budget INTEGER")


def downgrade() -> None:
    # Plain DROP COLUMN: recreating the table would need its foreign keys off.
    op.execute("ALTER TABLE projects DROP COLUMN remote_token_budget")
    op.execute("ALTER TABLE projects DROP COLUMN never_send")
