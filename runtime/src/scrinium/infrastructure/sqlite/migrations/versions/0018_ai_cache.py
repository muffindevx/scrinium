"""Remote answers kept to reuse for the very same request (T5, ADR-010).

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-14 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_cache",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("task", sa.String(length=32), nullable=False),
        sa.Column("answered_by", sa.String(length=300), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_ai_cache_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_ai_cache_project_id_projects"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_cache"),
        sa.UniqueConstraint("project_id", "key", name="uq_ai_cache_project_id_key"),
    )
    op.create_index("ix_ai_cache_workspace_id", "ai_cache", ["workspace_id"])
    op.create_index("ix_ai_cache_project_id", "ai_cache", ["project_id"])
    op.create_index("ix_ai_cache_created_at", "ai_cache", ["created_at"])


def downgrade() -> None:
    op.drop_table("ai_cache")
