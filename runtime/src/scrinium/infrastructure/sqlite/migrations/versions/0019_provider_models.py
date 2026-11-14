"""The models each remote provider offers that this workspace uses (ADR-010).

Filled from the consents in force, so the models already in use appear in the catalog.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-14 23:00:00.000000
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_models",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_provider_models_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["model_providers.id"],
            name="fk_provider_models_provider_id_model_providers", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_provider_models"),
        sa.UniqueConstraint(
            "provider_id", "model", name="uq_provider_models_provider_id_model"
        ),
    )  # fmt: skip
    op.create_index("ix_provider_models_workspace_id", "provider_models", ["workspace_id"])
    op.create_index("ix_provider_models_provider_id", "provider_models", ["provider_id"])
    connection = op.get_bind()
    used = connection.execute(
        sa.text(
            "SELECT DISTINCT workspace_id, provider_id, model FROM ai_consents "
            "WHERE revoked_at IS NULL AND provider_id IS NOT NULL"
        )
    ).fetchall()
    now = datetime.now(UTC).replace(tzinfo=None)
    for workspace_id, provider_id, model in used:
        connection.execute(
            sa.text(
                "INSERT INTO provider_models (id, workspace_id, provider_id, model, created_at) "
                "VALUES (:id, :workspace, :provider, :model, :now)"
            ),
            {"id": uuid.uuid4().hex, "workspace": workspace_id, "provider": provider_id,
             "model": model, "now": now},
        )  # fmt: skip


def downgrade() -> None:
    op.drop_table("provider_models")
