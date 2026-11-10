"""Alembic environment.

Scrinium migrates at startup and passes its own connection. To create a migration by hand from
`runtime/`: `uv run alembic revision --autogenerate -m "..."`, which uses `SCRINIUM_DATA_DIR`.
"""

from alembic import context
from sqlalchemy import Connection, create_engine

from scrinium.core.config import default_data_dir
from scrinium.infrastructure.sqlite.database import DATABASE_FILENAME, include_in_autogenerate
from scrinium.infrastructure.sqlite.models import Base


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        # SQLite cannot alter columns: Alembic recreates the table.
        render_as_batch=True,
        compare_type=True,
        include_name=include_in_autogenerate,
    )
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:
    engine = create_engine(f"sqlite:///{default_data_dir() / DATABASE_FILENAME}")
    with engine.begin() as own_connection:
        _run(own_connection)
    engine.dispose()
