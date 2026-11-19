"""Phase 10, the rest: Jira, per-connection options, and ticket text when a person allows it.

- `ticket_connections.provider` admits `jira` (Cloud and Data Center).
- `ticket_connections.settings`: the connection's options, all off by default — its base URL
  (Jira), whether descriptions and comments are read (10.6), whether Scrinium may comment on a
  ticket (10.7), and the account e-mail Jira Cloud's API tokens go with.
- `tickets.description` and `tickets.comments`: only filled when that connection allows reading
  text; emptied when it stops. Comments keep their text, never their author (ADR-014).

SQLite cannot change a CHECK, so `ticket_connections` is recreated; `tickets` references it and
`Database.migrate` checks the links with `foreign_key_check` afterwards.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = ("linear",)
_NEW = ("linear", "jira")


def _provider(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(*values, name="ticket_provider", native_enum=False, create_constraint=True,
                   length=32)  # fmt: skip


def upgrade() -> None:
    with op.batch_alter_table("ticket_connections", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_ticket_connections_ticket_provider"), type_="check")
        batch.alter_column("provider", type_=_provider(_NEW), existing_nullable=False)
        batch.add_column(sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"))
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch.add_column(sa.Column("comments", sa.JSON(), nullable=True))


def downgrade() -> None:
    # Development only: fails if a Jira connection exists.
    with op.batch_alter_table("tickets") as batch:
        batch.drop_column("comments")
        batch.drop_column("description")
    with op.batch_alter_table("ticket_connections", recreate="always") as batch:
        batch.drop_column("settings")
        batch.drop_constraint(op.f("ck_ticket_connections_ticket_provider"), type_="check")
        batch.alter_column("provider", type_=_provider(_OLD), existing_nullable=False)
