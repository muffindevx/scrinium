"""Ticket connectors, read-only, Linear first (phase 10.1, ADR-014).

Three tables:

- `ticket_connections`: a tracker connected to a project; the token is a keyring reference;
- `tickets`: each ticket's metadata, identified by the provider's immutable id, with its key
  history and a tombstone for tickets deleted or no longer visible;
- `ticket_links`: what points to a ticket (a rule, a commit, a pull request) and how sure that
  is (explicit, mentioned, inferred, confirmed).

Tickets and links belong to their connection: removing it removes them (ON DELETE CASCADE).

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS = ("pending", "active", "error", "revoked", "disabled")
_LEVELS = ("explicit", "mentioned", "inferred", "confirmed")


def upgrade() -> None:
    op.create_table(
        "ticket_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "linear",
                name="ticket_provider",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                *_STATUS,
                name="connection_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("auth", sa.String(length=16), nullable=False),
        sa.Column("secret_ref", sa.String(length=200), nullable=True),
        sa.Column("key_hint", sa.String(length=4), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("cursor", sa.JSON(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("last_sweep_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_ticket_connections_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_ticket_connections_workspace_id_workspaces",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ticket_connections"),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            "project_id",
            name="uq_ticket_connections_id_workspace_id_project_id",
        ),
    )
    op.create_index("ix_ticket_connections_project_id", "ticket_connections", ["project_id"])
    op.create_index("ix_ticket_connections_workspace_id", "ticket_connections", ["workspace_id"])

    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("container_id", sa.String(length=100), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("previous_keys", sa.JSON(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("ticket_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=100), nullable=False),
        sa.Column("status_category", sa.String(length=16), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("tombstone", sa.Boolean(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id", "workspace_id", "project_id"],
            [
                "ticket_connections.id",
                "ticket_connections.workspace_id",
                "ticket_connections.project_id",
            ],
            name="fk_tickets_connection_id_workspace_id_project_id_ticket_connections",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_tickets_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_tickets_workspace_id_workspaces"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tickets"),
        sa.UniqueConstraint(
            "connection_id", "external_id", name="uq_tickets_connection_id_external_id"
        ),
        sa.UniqueConstraint(
            "id", "workspace_id", "project_id", name="uq_tickets_id_workspace_id_project_id"
        ),
    )
    op.create_index("ix_tickets_connection_id", "tickets", ["connection_id"])
    op.create_index("ix_tickets_key", "tickets", ["key"])
    op.create_index("ix_tickets_project_id", "tickets", ["project_id"])
    op.create_index("ix_tickets_workspace_id", "tickets", ["workspace_id"])

    op.create_table(
        "ticket_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("from_kind", sa.String(length=16), nullable=False),
        sa.Column("from_ref", sa.String(length=500), nullable=False),
        sa.Column(
            "level",
            sa.Enum(
                *_LEVELS,
                name="ticket_link_level",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("origin", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id", "workspace_id", "project_id"],
            ["tickets.id", "tickets.workspace_id", "tickets.project_id"],
            name="fk_ticket_links_ticket_id_workspace_id_project_id_tickets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_ticket_links_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_ticket_links_workspace_id_workspaces"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ticket_links"),
        sa.UniqueConstraint(
            "ticket_id",
            "from_kind",
            "from_ref",
            "level",
            name="uq_ticket_links_ticket_id_from_kind_from_ref_level",
        ),
    )
    op.create_index("ix_ticket_links_from_ref", "ticket_links", ["from_ref"])
    op.create_index("ix_ticket_links_project_id", "ticket_links", ["project_id"])
    op.create_index("ix_ticket_links_ticket_id", "ticket_links", ["ticket_id"])
    op.create_index("ix_ticket_links_workspace_id", "ticket_links", ["workspace_id"])


def downgrade() -> None:
    op.drop_table("ticket_links")
    op.drop_table("tickets")
    op.drop_table("ticket_connections")
