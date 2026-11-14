"""A local record of every request sent to a remote model, without its content (ADR-010).

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-14 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("consent_id", sa.Uuid(), nullable=True),
        sa.Column("task", sa.String(length=32), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("answered_by", sa.String(length=300), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("sent_chars", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_ai_requests_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_ai_requests_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["consent_id"], ["ai_consents.id"],
            name="fk_ai_requests_consent_id_ai_consents", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_requests"),
    )  # fmt: skip
    op.create_index("ix_ai_requests_workspace_id", "ai_requests", ["workspace_id"])
    op.create_index("ix_ai_requests_project_id", "ai_requests", ["project_id"])
    op.create_index("ix_ai_requests_consent_id", "ai_requests", ["consent_id"])
    op.create_index("ix_ai_requests_created_at", "ai_requests", ["created_at"])


def downgrade() -> None:
    op.drop_table("ai_requests")
