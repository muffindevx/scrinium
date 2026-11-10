"""Code symbols (functions, methods, classes) of each revision.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-09 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "code_symbols",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_revision_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("qualified_name", sa.String(length=1000), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("logic_hash", sa.String(length=64), nullable=False),
        sa.Column("calls", sa.JSON(), nullable=False),
        sa.Column("structure_version", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_revision_id"],
            ["source_revisions.id"],
            name="fk_code_symbols_source_revision_id_source_revisions",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_code_symbols_workspace_id_workspaces"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_code_symbols"),
    )
    op.create_index("ix_code_symbols_workspace_id", "code_symbols", ["workspace_id"])
    op.create_index("ix_code_symbols_source_revision_id", "code_symbols", ["source_revision_id"])
    op.create_index("ix_code_symbols_name", "code_symbols", ["name"])
    op.create_index("ix_code_symbols_logic_hash", "code_symbols", ["logic_hash"])


def downgrade() -> None:
    op.drop_table("code_symbols")
