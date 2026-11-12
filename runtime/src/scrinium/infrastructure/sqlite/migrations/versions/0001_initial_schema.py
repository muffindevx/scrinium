"""Initial schema: workspace, projects, sources, revisions and audit log.

Revision ID: 0001
Revises:
Create Date: 2026-11-07 09:56:35.561624
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Source revisions are immutable and the audit log only accepts new rows.
# Only UPDATE is blocked, so deleting workspace data stays possible. SQLite syntax:
# PostgreSQL will need its own migration.
_APPEND_ONLY_TABLES = ("source_revisions", "audit_events")


def upgrade() -> None:
    op.create_table(
        "local_profile",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_local_profile_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_local_profile")),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "workspace_type",
            sa.Enum(
                "personal_local",
                name="workspace_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.String(length=200), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=200), nullable=True),
        sa.Column(
            "result",
            sa.Enum(
                "success",
                "denied",
                "failure",
                name="audit_result",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_audit_events_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_audit_events_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_audit_events_workspace_id"), ["workspace_id"], unique=False
        )

    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "archived",
                name="project_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_projects_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint(
            "workspace_id", "project_key", name=op.f("uq_projects_workspace_id_project_key")
        ),
    )
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_projects_workspace_id"), ["workspace_id"], unique=False
        )

    op.create_table(
        "source_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("external_account_id", sa.String(length=500), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "active",
                "error",
                "revoked",
                "disabled",
                name="connection_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("secret_ref", sa.String(length=200), nullable=True),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("last_successful_sync_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_source_connections_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_connections")),
        sa.UniqueConstraint(
            "id", "workspace_id", name=op.f("uq_source_connections_id_workspace_id")
        ),
    )
    with op.batch_alter_table("source_connections", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_source_connections_workspace_id"), ["workspace_id"], unique=False
        )

    op.create_table(
        "source_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_connection_id", sa.Uuid(), nullable=False),
        sa.Column("provider_item_id", sa.String(length=1000), nullable=False),
        sa.Column("item_type", sa.String(length=64), nullable=False),
        sa.Column("canonical_uri", sa.String(length=2000), nullable=False),
        sa.Column("title", sa.String(length=1000), nullable=True),
        sa.Column("author_ref", sa.String(length=500), nullable=True),
        sa.Column("created_at_source", sa.DateTime(), nullable=True),
        sa.Column("updated_at_source", sa.DateTime(), nullable=True),
        sa.Column("access_metadata", sa.JSON(), nullable=False),
        sa.Column(
            "lifecycle_status",
            sa.Enum(
                "active",
                "deleted",
                "inaccessible",
                "archived",
                name="item_lifecycle",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_connection_id", "workspace_id"],
            ["source_connections.id", "source_connections.workspace_id"],
            name=op.f("fk_source_items_source_connection_id_workspace_id_source_connections"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_source_items_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_items")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_source_items_id_workspace_id")),
        sa.UniqueConstraint(
            "source_connection_id",
            "provider_item_id",
            name=op.f("uq_source_items_source_connection_id_provider_item_id"),
        ),
    )
    with op.batch_alter_table("source_items", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_source_items_workspace_id"), ["workspace_id"], unique=False
        )

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_connection_id", sa.Uuid(), nullable=False),
        sa.Column(
            "trigger",
            sa.Enum(
                "initial",
                "webhook",
                "scheduled",
                "manual",
                "reconciliation",
                name="sync_trigger",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "succeeded",
                "partial",
                "failed",
                "cancelled",
                name="sync_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("cursor_before", sa.String(length=1000), nullable=True),
        sa.Column("cursor_after", sa.String(length=1000), nullable=True),
        sa.Column("items_seen", sa.Integer(), nullable=False),
        sa.Column("items_created", sa.Integer(), nullable=False),
        sa.Column("items_updated", sa.Integer(), nullable=False),
        sa.Column("items_deleted", sa.Integer(), nullable=False),
        sa.Column("items_failed", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error_summary", sa.String(length=1000), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_connection_id", "workspace_id"],
            ["source_connections.id", "source_connections.workspace_id"],
            name=op.f("fk_sync_runs_source_connection_id_workspace_id_source_connections"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_sync_runs_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_runs")),
        sa.UniqueConstraint(
            "source_connection_id",
            "idempotency_key",
            name=op.f("uq_sync_runs_source_connection_id_idempotency_key"),
        ),
    )
    with op.batch_alter_table("sync_runs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_sync_runs_source_connection_id"), ["source_connection_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_sync_runs_workspace_id"), ["workspace_id"], unique=False
        )

    op.create_table(
        "source_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_item_id", sa.Uuid(), nullable=False),
        sa.Column("revision_key", sa.String(length=200), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("storage_ref", sa.String(length=500), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("parser_name", sa.String(length=100), nullable=False),
        sa.Column("parser_version", sa.String(length=50), nullable=False),
        sa.CheckConstraint(
            "(text_content IS NULL) <> (storage_ref IS NULL)",
            name=op.f("ck_source_revisions_one_content_location"),
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64", name=op.f("ck_source_revisions_content_hash_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["source_item_id", "workspace_id"],
            ["source_items.id", "source_items.workspace_id"],
            name=op.f("fk_source_revisions_source_item_id_workspace_id_source_items"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_source_revisions_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_revisions")),
        sa.UniqueConstraint(
            "source_item_id",
            "revision_key",
            name=op.f("uq_source_revisions_source_item_id_revision_key"),
        ),
    )
    with op.batch_alter_table("source_revisions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_source_revisions_content_hash"), ["content_hash"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_source_revisions_source_item_id"), ["source_item_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_source_revisions_workspace_id"), ["workspace_id"], unique=False
        )

    for table in _APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} es de solo anadir'); END"
        )


def downgrade() -> None:
    for table in _APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER {table}_no_update")
    with op.batch_alter_table("source_revisions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_source_revisions_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_source_revisions_source_item_id"))
        batch_op.drop_index(batch_op.f("ix_source_revisions_content_hash"))

    op.drop_table("source_revisions")
    with op.batch_alter_table("sync_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sync_runs_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_sync_runs_source_connection_id"))

    op.drop_table("sync_runs")
    with op.batch_alter_table("source_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_source_items_workspace_id"))

    op.drop_table("source_items")
    with op.batch_alter_table("source_connections", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_source_connections_workspace_id"))

    op.drop_table("source_connections")
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_projects_workspace_id"))

    op.drop_table("projects")
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_events_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_audit_events_created_at"))

    op.drop_table("audit_events")
    op.drop_table("workspaces")
    op.drop_table("local_profile")
