"""SQLite, migrations and schema integrity, against a real file."""

import hashlib
import stat
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError, StatementError

from scrinium.application.workspaces import ensure_local_workspace
from scrinium.domain.enums import AiMode, AuditResult, WorkspaceType
from scrinium.infrastructure.sqlite.database import (
    Database,
    IncompatibleDatabaseError,
    alembic_config,
    include_in_autogenerate,
)
from scrinium.infrastructure.sqlite.models import (
    AuditEvent,
    Base,
    LocalProfile,
    Project,
    SourceConnection,
    SourceItem,
    SourceRevision,
    Workspace,
)

EXPECTED_TABLES = {
    "analysis_runs",
    "code_symbols",
    "code_edges",
    "content_chunks",
    "content_chunks_fts",
    "extraction_tasks",
    "workspaces",
    "projects",
    "local_profile",
    "source_connections",
    "sync_runs",
    "source_items",
    "source_revisions",
    "audit_events",
    "knowledge_entities",
    "knowledge_entity_versions",
    "business_rules",
    "business_rule_versions",
    "evidence_records",
    "entity_relationships",
    "findings",
    "finding_evidence",
    "software_releases",
    "release_knowledge_snapshots",
    "release_snapshot_entries",
    "flow_definitions",
    "flow_versions",
    "flow_steps",
    "flow_transitions",
}


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database.open(tmp_path / "data")
    yield db
    db.dispose()


def _workspace(db: Database) -> uuid.UUID:
    with db.session() as session:
        return ensure_local_workspace(session).id


def _item(db: Database, workspace_id: uuid.UUID) -> uuid.UUID:
    with db.session() as session:
        connection = SourceConnection(
            workspace_id=workspace_id, provider="local_repository", display_name="repo"
        )
        session.add(connection)
        session.flush()
        item = SourceItem(
            workspace_id=workspace_id,
            source_connection_id=connection.id,
            provider_item_id="README.md",
            item_type="file",
            canonical_uri="file:///repo/README.md",
        )
        session.add(item)
        session.flush()
        return item.id


def _revision(workspace_id: uuid.UUID, item_id: uuid.UUID, key: str = "abc") -> SourceRevision:
    content = "hola"
    return SourceRevision(
        workspace_id=workspace_id,
        source_item_id=item_id,
        revision_key=key,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        text_content=content,
        parser_name="text",
        parser_version="1",
    )


def test_migrations_create_every_planned_table(database: Database) -> None:
    assert set(inspect(database.engine).get_table_names()) >= EXPECTED_TABLES


def test_migrations_match_the_models(database: Database) -> None:
    with database.engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"include_name": include_in_autogenerate}
        )
        differences = compare_metadata(context, Base.metadata)
    assert differences == []


def test_downgrade_and_upgrade_again(database: Database) -> None:
    config = alembic_config()
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
    assert EXPECTED_TABLES.isdisjoint(inspect(database.engine).get_table_names())
    database.migrate()
    assert set(inspect(database.engine).get_table_names()) >= EXPECTED_TABLES


def test_database_is_private_to_this_account(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db = Database.open(data_dir)
    _workspace(db)
    db.dispose()
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    for path in data_dir.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name


def test_wal_and_foreign_keys_are_on(database: Database) -> None:
    with database.engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_local_workspace_is_created_once(tmp_path: Path) -> None:
    for _ in range(2):
        db = Database.open(tmp_path / "data")
        _workspace(db)
        db.dispose()
    db = Database.open(tmp_path / "data")
    with db.session() as session:
        workspaces = session.scalars(select(Workspace)).all()
        assert [w.workspace_type for w in workspaces] == [WorkspaceType.PERSONAL_LOCAL]
        assert session.get(LocalProfile, 1) is not None
        created = select(func.count()).where(AuditEvent.action == "workspace.create")
        assert session.scalar(created) == 1
    db.dispose()


def test_newer_database_is_refused(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "data")
    with db.engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = '9999'"))
    with pytest.raises(IncompatibleDatabaseError):
        db.migrate()
    db.dispose()


def test_only_one_local_profile_row(database: Database) -> None:
    _workspace(database)
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(LocalProfile(id=2))


def test_foreign_keys_are_enforced(database: Database) -> None:
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(Project(workspace_id=uuid.uuid4(), name="x", project_key="x"))


def test_child_cannot_point_to_a_parent_in_another_workspace(database: Database) -> None:
    workspace_id = _workspace(database)
    item_id = _item(database, workspace_id)
    with database.session() as session:
        other = Workspace(name="otro", workspace_type=WorkspaceType.PERSONAL_LOCAL)
        session.add(other)
        session.flush()
        other_id = other.id
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(_revision(other_id, item_id))


def test_values_outside_the_vocabulary_are_rejected(database: Database) -> None:
    workspace_id = _workspace(database)
    with pytest.raises(StatementError), database.session() as session:
        session.add(Project(workspace_id=workspace_id, name="x", project_key="x", status="deleted"))
    # Also raw SQL, without going through SQLAlchemy.
    with pytest.raises(IntegrityError), database.engine.begin() as connection:
        connection.execute(text("UPDATE workspaces SET workspace_type = 'team'"))


def test_a_project_is_local_unless_someone_consents(database: Database) -> None:
    """ADR-010: a new project, or one from before the column existed, is `local_only`; the
    database itself refuses any other mode than the two it knows."""
    workspace_id = _workspace(database)
    with database.session() as session:
        session.add(Project(workspace_id=workspace_id, name="x", project_key="x"))
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, workspace_id, name, description, project_key, status, "
                "created_at, updated_at) VALUES ('00000000000000000000000000000001', :w, 'y', '', "
                "'y', 'active', '2026-11-14', '2026-11-14')"
            ),
            {"w": workspace_id.hex},
        )
    with database.session() as session:
        assert {p.ai_mode for p in session.scalars(select(Project))} == {AiMode.LOCAL_ONLY}
    with pytest.raises(IntegrityError), database.engine.begin() as connection:
        connection.execute(text("UPDATE projects SET ai_mode = 'anywhere'"))


def test_project_key_is_unique_per_workspace(database: Database) -> None:
    workspace_id = _workspace(database)
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(Project(workspace_id=workspace_id, name="a", project_key="pagos"))
        session.add(Project(workspace_id=workspace_id, name="b", project_key="pagos"))
        session.flush()


def test_same_revision_cannot_be_stored_twice(database: Database) -> None:
    workspace_id = _workspace(database)
    item_id = _item(database, workspace_id)
    with database.session() as session:
        session.add(_revision(workspace_id, item_id))
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(_revision(workspace_id, item_id))


def test_revision_needs_a_sha256_and_exactly_one_content_location(database: Database) -> None:
    workspace_id = _workspace(database)
    item_id = _item(database, workspace_id)
    bad_hash = _revision(workspace_id, item_id, "a")
    bad_hash.content_hash = "abc"
    both = _revision(workspace_id, item_id, "b")
    both.storage_ref = "objects/ab/cd"
    neither = _revision(workspace_id, item_id, "c")
    neither.text_content = None
    for revision in (bad_hash, both, neither):
        with pytest.raises(IntegrityError), database.session() as session:
            session.add(revision)


def test_revisions_are_immutable(database: Database) -> None:
    workspace_id = _workspace(database)
    item_id = _item(database, workspace_id)
    with database.session() as session:
        session.add(_revision(workspace_id, item_id))
    with pytest.raises(IntegrityError, match="solo anadir"), database.session() as session:
        session.execute(update(SourceRevision).values(text_content="cambiado"))


def test_audit_events_are_append_only(database: Database) -> None:
    _workspace(database)
    with pytest.raises(IntegrityError, match="solo anadir"), database.engine.begin() as conn:
        conn.execute(update(AuditEvent).values(result=AuditResult.FAILURE))


def test_dates_come_back_in_utc(database: Database) -> None:
    workspace_id = _workspace(database)
    madrid = datetime(2026, 11, 7, 10, 0, tzinfo=timezone(timedelta(hours=1)))
    with database.session() as session:
        connection = SourceConnection(
            workspace_id=workspace_id,
            provider="github",
            display_name="gh",
            last_successful_sync_at=madrid,
        )
        session.add(connection)
        session.flush()
        connection_id = connection.id
    with database.session() as session:
        stored = session.get(SourceConnection, connection_id)
        assert stored is not None
        assert stored.last_successful_sync_at == madrid
        assert stored.last_successful_sync_at.tzinfo == UTC


def test_naive_dates_are_rejected(database: Database) -> None:
    workspace_id = _workspace(database)
    with pytest.raises(StatementError), database.session() as session:
        session.add(
            SourceConnection(
                workspace_id=workspace_id,
                provider="github",
                display_name="gh",
                last_successful_sync_at=datetime(2026, 1, 1),
            )
        )


def test_upgrade_keeps_existing_rows(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "data")
    config = alembic_config()
    with db.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0001")
    with db.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workspaces VALUES "
                "('a1', 'w', 'personal_local', '{}', '2026-01-01', '2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO source_connections (id, workspace_id, provider, display_name, "
                "status, scopes, configuration, created_at, updated_at) VALUES "
                "('c1', 'a1', 'github', 'gh', 'active', '[]', '{}', '2026-01-01', '2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sync_runs (id, workspace_id, source_connection_id, trigger, status, "
                "items_seen, items_created, items_updated, items_deleted, items_failed, "
                "created_at) VALUES ('s1', 'a1', 'c1', 'manual', 'queued', 0, 0, 0, 0, 0, "
                "'2026-01-01')"
            )
        )
    db.migrate()
    with db.engine.connect() as connection:
        row = connection.execute(text("SELECT items_excluded FROM sync_runs")).one()
        assert row.items_excluded == 0
        assert (
            connection.execute(text("SELECT project_id FROM source_connections")).scalar() is None
        )
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    db.dispose()


def test_recreating_analysis_runs_keeps_its_children(tmp_path: Path) -> None:
    """0006 recreates `analysis_runs` to change its CHECK; its findings must stay linked."""
    db = Database.open(tmp_path / "data")
    config = alembic_config()
    with db.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0005")
    with db.engine.begin() as connection:
        for statement in (
            "INSERT INTO workspaces VALUES ('a1', 'w', 'personal_local', '{}', '2026-01-01', "
            "'2026-01-01')",
            "INSERT INTO projects VALUES ('p1', 'a1', 'P', '', 'p', 'active', '2026-01-01', "
            "'2026-01-01')",
            "INSERT INTO analysis_runs (id, workspace_id, project_id, analysis_type, question, "
            "input_hash, status, result, prompt_version, retrieval_version, created_at) VALUES "
            "('r1', 'a1', 'p1', 'question', 'q', 'h', 'completed', '{}', '2', '1', '2026-01-01')",
            "INSERT INTO findings (id, workspace_id, project_id, analysis_run_id, finding_type, "
            "title, description, severity, assessment_status, created_at) VALUES ('f1', 'a1', "
            "'p1', 'r1', 'unsupported_claim', 't', 'd', 'info', 'hypothesis', '2026-01-01')",
        ):
            connection.execute(text(statement))
    db.migrate()
    with db.engine.begin() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        assert connection.execute(text("SELECT analysis_run_id FROM findings")).scalar() == "r1"
        connection.execute(text("UPDATE analysis_runs SET analysis_type = 'sql_review'"))
    with pytest.raises(IntegrityError), db.engine.begin() as connection:
        connection.execute(text("UPDATE analysis_runs SET analysis_type = 'otro'"))
    # Foreign keys are enforced again after migrating.
    with pytest.raises(IntegrityError), db.engine.begin() as connection:
        connection.execute(text("DELETE FROM analysis_runs"))
    db.dispose()
