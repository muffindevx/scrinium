"""Each task's default model per project, and the model a run chose (ADR-010).

Until now a task had one permitted model, which was also its model. Permissions and defaults
are now apart: the defaults start as the models the consents in force gave each task.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-15 00:00:00.000000
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN task_models JSON NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE analysis_runs ADD COLUMN model_choice VARCHAR(300)")
    connection = op.get_bind()
    defaults: dict[str, dict[str, dict[str, str]]] = {}
    for project_id, provider_id, model, tasks in connection.execute(
        sa.text(
            "SELECT project_id, provider_id, model, tasks FROM ai_consents "
            "WHERE revoked_at IS NULL AND provider_id IS NOT NULL ORDER BY created_at"
        )
    ):
        chosen = defaults.setdefault(project_id, {})
        for task in json.loads(tasks) if isinstance(tasks, str) else tasks:
            chosen[task] = {"provider_id": str(uuid.UUID(provider_id)), "model": model}
    for project_id, chosen in defaults.items():
        connection.execute(
            sa.text("UPDATE projects SET task_models = :value WHERE id = :id"),
            {"value": json.dumps(chosen), "id": project_id},
        )


def downgrade() -> None:
    # Plain DROP COLUMN: recreating the table would need its foreign keys off.
    op.execute("ALTER TABLE analysis_runs DROP COLUMN model_choice")
    op.execute("ALTER TABLE projects DROP COLUMN task_models")
