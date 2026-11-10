"""Question history: the analysis_runs table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-07 12:47:37.457308
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "analysis_type",
            sa.Enum(
                "question",
                name="analysis_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "completed",
                "failed",
                name="analysis_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "answer_status",
            sa.Enum(
                "answered",
                "insufficient_evidence",
                name="answer_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("model_provider", sa.String(length=100), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=20), nullable=False),
        sa.Column("retrieval_version", sa.String(length=20), nullable=False),
        sa.Column("error_summary", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_analysis_runs_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_analysis_runs_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_runs")),
    )
    op.create_index("ix_analysis_runs_created_at", "analysis_runs", ["created_at"], unique=False)
    op.create_index("ix_analysis_runs_project_id", "analysis_runs", ["project_id"], unique=False)
    op.create_index(
        "ix_analysis_runs_workspace_id", "analysis_runs", ["workspace_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_runs_workspace_id", "analysis_runs")
    op.drop_index("ix_analysis_runs_project_id", "analysis_runs")
    op.drop_index("ix_analysis_runs_created_at", "analysis_runs")

    op.drop_table("analysis_runs")
