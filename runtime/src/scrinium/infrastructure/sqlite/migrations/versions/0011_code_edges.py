"""Resolved calls between the project's functions (stage 3, phase B). Derived.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-12 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "code_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("from_symbol_id", sa.Uuid(), nullable=False),
        sa.Column("to_symbol_id", sa.Uuid(), nullable=True),
        sa.Column("callee", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["from_symbol_id"], ["code_symbols.id"],
            name="fk_code_edges_from_symbol_id_code_symbols", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_symbol_id"], ["code_symbols.id"],
            name="fk_code_edges_to_symbol_id_code_symbols", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_code_edges_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_code_edges_workspace_id_workspaces"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_code_edges"),
    )  # fmt: skip
    op.create_index("ix_code_edges_workspace_id", "code_edges", ["workspace_id"])
    op.create_index("ix_code_edges_project_id", "code_edges", ["project_id"])
    op.create_index("ix_code_edges_from_symbol_id", "code_edges", ["from_symbol_id"])
    op.create_index("ix_code_edges_to_symbol_id", "code_edges", ["to_symbol_id"])


def downgrade() -> None:
    op.drop_table("code_edges")
