"""Each project's AI mode: local unless a person consents to a remote model (ADR-010).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-14 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every existing project starts local: nothing was ever consented.
    op.execute(
        "ALTER TABLE projects ADD COLUMN ai_mode VARCHAR(32) NOT NULL DEFAULT 'local_only' "
        "CONSTRAINT ai_mode CHECK (ai_mode IN ('local_only', 'remote_allowed'))"
    )


def downgrade() -> None:
    # Plain DROP COLUMN: recreating the table would need its foreign keys off.
    op.execute("ALTER TABLE projects DROP COLUMN ai_mode")
