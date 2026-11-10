"""Persistent rule-extraction queue.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-09 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extraction_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_revision_id", sa.Uuid(), nullable=False),
        sa.Column("unit_key", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("symbol", sa.String(length=1000), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("rules_found", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_extraction_tasks_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["source_revision_id"],
            ["source_revisions.id"],
            name="fk_extraction_tasks_source_revision_id_source_revisions",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_extraction_tasks_workspace_id_workspaces"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_extraction_tasks"),
        sa.UniqueConstraint(
            "project_id", "unit_key", name="uq_extraction_tasks_project_id_unit_key"
        ),
    )
    op.create_index("ix_extraction_tasks_workspace_id", "extraction_tasks", ["workspace_id"])
    op.create_index("ix_extraction_tasks_project_id", "extraction_tasks", ["project_id"])
    op.create_index(
        "ix_extraction_tasks_source_revision_id", "extraction_tasks", ["source_revision_id"]
    )
    op.create_index("ix_extraction_tasks_status", "extraction_tasks", ["status"])


def downgrade() -> None:
    op.drop_table("extraction_tasks")
