"""New analysis types (extraction, diff, SQL, releases, bugs) and their references.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-07 16:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# SQLite cannot change a CHECK: `analysis_runs` must be recreated. Its children (`findings`,
# `business_rule_versions`) reference it, so `Database.migrate` turns off foreign keys
# during the migration and checks with `foreign_key_check` that everything is still linked.

_OLD_TYPES = ("question",)
_NEW_TYPES = (
    "question",
    "knowledge_extraction",
    "code_diff",
    "sql_review",
    "release_comparison",
    "impact_analysis",
    "bug_investigation",
)
_OLD_STATUSES = ("queued", "running", "completed", "failed")
_NEW_STATUSES = ("queued", "running", "completed", "partial", "failed", "cancelled")


def _enum(values: tuple[str, ...], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def _alter(types: tuple[str, ...], statuses: tuple[str, ...]) -> None:
    with op.batch_alter_table("analysis_runs", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_analysis_runs_analysis_type"), type_="check")
        batch.drop_constraint(op.f("ck_analysis_runs_analysis_status"), type_="check")
        batch.alter_column(
            "analysis_type", type_=_enum(types, "analysis_type"), existing_nullable=False
        )
        batch.alter_column(
            "status", type_=_enum(statuses, "analysis_status"), existing_nullable=False
        )
        if types == _NEW_TYPES:
            batch.add_column(sa.Column("base_revision", sa.String(length=200), nullable=True))
            batch.add_column(sa.Column("target_revision", sa.String(length=200), nullable=True))
            batch.add_column(sa.Column("release_ref", sa.String(length=200), nullable=True))
        else:
            batch.drop_column("release_ref")
            batch.drop_column("target_revision")
            batch.drop_column("base_revision")


def upgrade() -> None:
    _alter(_NEW_TYPES, _NEW_STATUSES)


def downgrade() -> None:
    # Development only: fails if there are analyses of the new types or statuses.
    _alter(_OLD_TYPES, _OLD_STATUSES)
