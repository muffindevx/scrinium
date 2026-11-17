"""The decision nodes of each file revision, for coverage (phase 8.1).

Coverage needs, for every file, its decisions (guards, thresholds, tables…) and which have a
rule. Parsing 10 000 files on each visit is too slow; a revision never changes, so its
decisions are computed once and kept here.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "revision_decisions",
        sa.Column("source_revision_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("extractor_version", sa.String(length=20), nullable=False),
        sa.Column("lines", sa.Integer(), nullable=False),
        sa.Column("spans", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_revision_id"],
            ["source_revisions.id"],
            name="fk_revision_decisions_source_revision_id_source_revisions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_revision_decisions_workspace_id_workspaces",
        ),
        sa.PrimaryKeyConstraint("source_revision_id", name="pk_revision_decisions"),
    )
    op.create_index("ix_revision_decisions_workspace_id", "revision_decisions", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_revision_decisions_workspace_id", "revision_decisions")
    op.drop_table("revision_decisions")
