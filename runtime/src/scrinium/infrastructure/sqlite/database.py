"""SQLite connection and migrations at startup."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

DATABASE_FILENAME: Final = "scrinium.sqlite3"
MIGRATIONS_DIR: Final = Path(__file__).resolve().parent / "migrations"


# Tables the models do not describe: the FTS5 index and its internal tables.
_UNMODELED_TABLE_PREFIX: Final = "content_chunks_fts"


def include_in_autogenerate(name: str | None, type_: str, _parents: object) -> bool:
    """Alembic filter: only compares what the models describe."""
    return not (type_ == "table" and name is not None and name.startswith(_UNMODELED_TABLE_PREFIX))


class IncompatibleDatabaseError(RuntimeError):
    """The database was created by a newer version of Scrinium than this one."""


class BrokenMigrationError(RuntimeError):
    """After migrating, rows pointing to missing rows would remain."""


def _configure_connection(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        # SQLite does not enforce foreign keys unless asked on every connection.
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 5000")
        # Deleted data is overwritten with zeros, instead of staying in free pages.
        cursor.execute("PRAGMA secure_delete = ON")
    finally:
        cursor.close()


def _ensure_private_file(path: Path) -> None:
    # Created before SQLite so it is born 0600; the -wal and -shm files inherit the mode.
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


class Database:
    def __init__(self, engine: Engine, data_dir: Path | None = None) -> None:
        self.engine = engine
        # Where the database lives: deletion records a pending purge there (R3).
        self.data_dir = data_dir
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def open(cls, data_dir: Path) -> "Database":
        path = data_dir / DATABASE_FILENAME
        _ensure_private_file(path)
        engine = create_engine(
            f"sqlite:///{path}",
            # FastAPI serves `def` routes in a thread pool.
            connect_args={"check_same_thread": False},
        )
        event.listen(engine, "connect", _configure_connection)
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode = WAL")
        database = cls(engine, data_dir)
        database.migrate()
        return database

    @classmethod
    def open_read_only(cls, data_dir: Path) -> "Database":
        """The database as it is, for readers outside the running Scrinium (`scrinium mcp`):
        SQLite refuses every write, and nothing is migrated. Raises FileNotFoundError if there
        is none yet and `IncompatibleDatabaseError` if its schema is not this version's."""
        path = data_dir / DATABASE_FILENAME
        if not path.is_file():
            raise FileNotFoundError(path)
        engine = create_engine(
            f"sqlite:///file:{path}?mode=ro&uri=true",
            connect_args={"check_same_thread": False, "uri": True},
        )

        def configure(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA busy_timeout = 5000")
                cursor.execute("PRAGMA query_only = ON")
            finally:
                cursor.close()

        event.listen(engine, "connect", configure)
        heads = set(ScriptDirectory.from_config(alembic_config()).get_heads())
        with engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
        if current != heads:
            raise IncompatibleDatabaseError
        return cls(engine, data_dir)

    def migrate(self) -> None:
        config = alembic_config()
        known = {script.revision for script in ScriptDirectory.from_config(config).walk_revisions()}
        with self.engine.connect() as connection:
            # Recreating a table with children (the only way SQLite allows changing a CHECK)
            # requires foreign keys off, and they can only be changed outside a
            # transaction. At the end it checks that no row was orphaned.
            connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
            connection.commit()
            try:
                with connection.begin():
                    current = MigrationContext.configure(connection).get_current_heads()
                    if not set(current) <= known:
                        raise IncompatibleDatabaseError
                    config.attributes["connection"] = connection
                    command.upgrade(config, "head")
                    if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                        raise BrokenMigrationError
            finally:
                connection.exec_driver_sql("PRAGMA foreign_keys = ON")
                connection.commit()

    def purge(self) -> None:
        """Removes from disk what was already deleted: FTS5 segments and the WAL.

        With `secure_delete` freed pages are already zeroed; the WAL still keeps copies
        of old pages until it is checkpointed and truncated. Call after committing.
        """
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO content_chunks_fts (content_chunks_fts) VALUES ('optimize')"
            )
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")

    def is_ready(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A transaction: commits on exit without errors and rolls back on an exception."""
        with self._sessions.begin() as session:
            yield session

    @contextmanager
    def scratch(self) -> Iterator[Session]:
        """A transaction that is always rolled back: to work out what something would do."""
        session = self._sessions()
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
