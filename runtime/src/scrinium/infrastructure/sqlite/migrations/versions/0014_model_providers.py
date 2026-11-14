"""Remote model providers and per-project consent to use them (ADR-010).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-14 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_providers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("openai_compatible", "anthropic", name="provider_kind", native_enum=False,
                    create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("secret_ref", sa.String(length=200), nullable=True),
        sa.Column("key_hint", sa.String(length=4), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_model_providers_workspace_id_workspaces"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_providers"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_model_providers_workspace_id_name"),
    )  # fmt: skip
    op.create_index("ix_model_providers_workspace_id", "model_providers", ["workspace_id"])
    op.create_table(
        "ai_consents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=True),
        sa.Column("provider_name", sa.String(length=100), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("tasks", sa.JSON(), nullable=False),
        sa.Column("retention", sa.JSON(), nullable=False),
        sa.Column("consented_by", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_ai_consents_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_ai_consents_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["model_providers.id"],
            name="fk_ai_consents_provider_id_model_providers", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_consents"),
    )  # fmt: skip
    op.create_index("ix_ai_consents_workspace_id", "ai_consents", ["workspace_id"])
    op.create_index("ix_ai_consents_project_id", "ai_consents", ["project_id"])
    op.create_index("ix_ai_consents_provider_id", "ai_consents", ["provider_id"])


def downgrade() -> None:
    op.drop_table("ai_consents")
    op.drop_table("model_providers")
