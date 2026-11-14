"""Scrinium's canonical schema.

Portable types only: the same schema must be able to migrate to PostgreSQL in Hybrid.
Child tables repeat `workspace_id` and tie it with a composite foreign key, so a
row cannot point to a parent in another workspace.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Dialect,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from scrinium.domain.enums import (
    AiMode,
    AnalysisStatus,
    AnalysisType,
    AnswerStatus,
    AssessmentStatus,
    AuditResult,
    ConnectionStatus,
    Criticality,
    EntityLifecycle,
    EntityType,
    EvidenceRelevance,
    EvidenceRole,
    EvidenceType,
    FindingType,
    FlowStepType,
    FlowType,
    ItemLifecycle,
    ProjectStatus,
    ProviderKind,
    RelationType,
    ReleaseStatus,
    RuleCategory,
    Severity,
    SyncStatus,
    SyncTrigger,
    TransitionType,
    VerificationMethod,
    VerificationStatus,
    VersionOrigin,
    WorkspaceType,
)

# Stable names: Alembic needs them to alter constraints in SQLite.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """Stores UTC without a zone and returns it with one: SQLite drops the time zone on read."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime without a time zone")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


def _enum(values: type[StrEnum], name: str) -> Enum:
    # VARCHAR with CHECK instead of a native type: the same in SQLite and PostgreSQL.
    return Enum(
        values,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
        length=32,
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, onupdate=utc_now)


class Workspace(Timestamps, Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    workspace_type: Mapped[WorkspaceType] = mapped_column(_enum(WorkspaceType, "workspace_type"))
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Project(Timestamps, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("workspace_id", "project_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    project_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[ProjectStatus] = mapped_column(
        _enum(ProjectStatus, "project_status"), default=ProjectStatus.ACTIVE
    )
    # ADR-010: local unless a person consents to a remote model for this project.
    ai_mode: Mapped[AiMode] = mapped_column(
        _enum(AiMode, "ai_mode"), default=AiMode.LOCAL_ONLY, server_default=AiMode.LOCAL_ONLY.value
    )
    # Paths indexed and searched here but never sent to a remote model (ADR-010), as source
    # exclusion patterns: `contracts/`, `*.sql`.
    never_send: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    # Tokens per calendar month (UTC) for remote calls; None: no limit (T6).
    remote_token_budget: Mapped[int | None] = mapped_column(Integer)
    # Masking before a remote request (off by default): personal data, and long literals.
    mask_personal: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    mask_literals: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class ModelProvider(Timestamps, Base):
    """A remote model provider registered on this machine (ADR-010).

    The API key lives only in the OS keyring (`secret_ref`, ADR-006) and is sent only to
    `host`, the host it was registered for. `secret_ref` is empty after a restore: the key stays
    in the keyring of the machine it was typed on, so it must be typed again.
    """

    __tablename__ = "model_providers"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[ProviderKind] = mapped_column(_enum(ProviderKind, "provider_kind"))
    base_url: Mapped[str] = mapped_column(String(500))
    host: Mapped[str] = mapped_column(String(253))
    secret_ref: Mapped[str | None] = mapped_column(String(200))
    # The key's last four characters, so a person can tell keys apart. Never more.
    key_hint: Mapped[str] = mapped_column(String(4), default="")


class ProviderModel(Base):
    """A model a provider offers that this workspace uses: the catalog people choose from.

    Removing it revokes the consents that named it; removing the provider removes its models.
    """

    __tablename__ = "provider_models"
    __table_args__ = (UniqueConstraint("provider_id", "model"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_providers.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)


class AiConsent(Base):
    """A person's consent to send one project's text to a remote model (ADR-010).

    Rows are kept after revocation (`revoked_at`): they record what was allowed and when.
    """

    __tablename__ = "ai_consents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_providers.id", ondelete="SET NULL"), index=True
    )
    # Copied, not only referenced: the record must still say where data went if the provider
    # is later removed.
    provider_name: Mapped[str] = mapped_column(String(100))
    host: Mapped[str] = mapped_column(String(253))
    model: Mapped[str] = mapped_column(String(200))
    tasks: Mapped[list[str]] = mapped_column(JSON, default=list)
    # The retention notice the person acknowledged: its text, its sources and when Scrinium
    # checked it (or that it was not verified).
    retention: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    consented_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class AiRequest(Base):
    """One request sent to a remote model: what left, never its content (ADR-010).

    The content is already in this machine's index; this record answers "what did the provider
    receive?": which files and lines, for which task, to which host and model, and its size.
    """

    __tablename__ = "ai_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    consent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_consents.id", ondelete="SET NULL"), index=True
    )
    task: Mapped[str] = mapped_column(String(32))
    host: Mapped[str] = mapped_column(String(253))
    # The model asked for, and the one that answered (empty if nothing answered).
    model: Mapped[str] = mapped_column(String(200))
    answered_by: Mapped[str] = mapped_column(String(300), default="")
    outcome: Mapped[str] = mapped_column(String(16))
    # `path:start-end` of every passage sent; capped (see `routing.MAX_SOURCES`).
    sources: Mapped[list[str]] = mapped_column(JSON, default=list)
    sent_chars: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # How many distinct values were masked in what was sent (never the values).
    masked: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, index=True)


class AiCacheEntry(Base):
    """A remote answer kept to be reused for the very same request (T5, ADR-010).

    `key` is the SHA-256 of what would be sent: host, model, task, output limits and the
    messages after redaction and masking. `text` is the answer as received, so with the same
    placeholders as what was sent: masked values are never stored here.
    """

    __tablename__ = "ai_cache"
    __table_args__ = (UniqueConstraint("project_id", "key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    task: Mapped[str] = mapped_column(String(32))
    answered_by: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, index=True)


class LocalProfile(Timestamps, Base):
    """This installation's settings. Not an account: the MVP has no users."""

    __tablename__ = "local_profile"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)
    display_name: Mapped[str | None] = mapped_column(String(200))


class SourceConnection(Timestamps, Base):
    __tablename__ = "source_connections"
    __table_args__ = (UniqueConstraint("id", "workspace_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    # Local sources belong to a project. Remote ones may serve several.
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), index=True)
    # Open vocabulary: each new connector adds its provider.
    provider: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(200))
    external_account_id: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[ConnectionStatus] = mapped_column(
        _enum(ConnectionStatus, "connection_status"), default=ConnectionStatus.PENDING
    )
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Keyring reference, never the token (ADR-006).
    secret_ref: Mapped[str | None] = mapped_column(String(200))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(200))
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_connection_id", "workspace_id"],
            ["source_connections.id", "source_connections.workspace_id"],
        ),
        UniqueConstraint("source_connection_id", "idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    source_connection_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    trigger: Mapped[SyncTrigger] = mapped_column(_enum(SyncTrigger, "sync_trigger"))
    status: Mapped[SyncStatus] = mapped_column(
        _enum(SyncStatus, "sync_status"), default=SyncStatus.QUEUED
    )
    cursor_before: Mapped[str | None] = mapped_column(String(1000))
    cursor_after: Mapped[str | None] = mapped_column(String(1000))
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_created: Mapped[int] = mapped_column(Integer, default=0)
    items_updated: Mapped[int] = mapped_column(Integer, default=0)
    items_deleted: Mapped[int] = mapped_column(Integer, default=0)
    items_failed: Mapped[int] = mapped_column(Integer, default=0)
    # Files the exclusions left out: they are not failures.
    items_excluded: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # Already sanitized by the writer: never a traceback or source content.
    error_summary: Mapped[str | None] = mapped_column(String(1000))
    idempotency_key: Mapped[str | None] = mapped_column(String(200))


class SourceItem(Timestamps, Base):
    __tablename__ = "source_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_connection_id", "workspace_id"],
            ["source_connections.id", "source_connections.workspace_id"],
        ),
        UniqueConstraint("source_connection_id", "provider_item_id"),
        UniqueConstraint("id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    source_connection_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    provider_item_id: Mapped[str] = mapped_column(String(1000))
    # Vocabulario abierto: document, ticket, commit, file...
    item_type: Mapped[str] = mapped_column(String(64))
    canonical_uri: Mapped[str] = mapped_column(String(2000))
    title: Mapped[str | None] = mapped_column(String(1000))
    author_ref: Mapped[str | None] = mapped_column(String(500))
    created_at_source: Mapped[datetime | None] = mapped_column(UtcDateTime)
    updated_at_source: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # Copy of the source permissions. It authorizes nothing by itself.
    access_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    lifecycle_status: Mapped[ItemLifecycle] = mapped_column(
        _enum(ItemLifecycle, "item_lifecycle"), default=ItemLifecycle.ACTIVE
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # The current revision. The most recent is not enough: if a file goes back to earlier
    # content, that revision is reused, and it is older than the one in between.
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "source_revisions.id",
            use_alter=True,
            name="fk_source_items_current_revision_id_source_revisions",
        )
    )


class SourceRevision(Base):
    """Immutable snapshot: a migration trigger rejects any UPDATE."""

    __tablename__ = "source_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_item_id", "workspace_id"], ["source_items.id", "source_items.workspace_id"]
        ),
        UniqueConstraint("source_item_id", "revision_key"),
        CheckConstraint("length(content_hash) = 64", name="content_hash_sha256"),
        CheckConstraint(
            "(text_content IS NULL) <> (storage_ref IS NULL)", name="one_content_location"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    source_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    revision_key: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    text_content: Mapped[str | None] = mapped_column(Text)
    storage_ref: Mapped[str | None] = mapped_column(String(500))
    # `metadata` is reserved by SQLAlchemy in declarative classes.
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    source_updated_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    captured_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    parser_name: Mapped[str] = mapped_column(String(100))
    parser_version: Mapped[str] = mapped_column(String(50))


class AuditEvent(Base):
    """Rows are only added: a migration trigger rejects any UPDATE."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    # No users in the MVP: empty until Hybrid has identities.
    actor_id: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(200))
    result: Mapped[AuditResult] = mapped_column(_enum(AuditResult, "audit_result"))
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, index=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class ContentChunk(Base):
    """A revision's chunk. Derived: it can be deleted and rebuilt from the revision.

    The text is also copied into the FTS5 table `content_chunks_fts`, kept by migration
    triggers. Vectors live outside SQLite, in the vector index.
    """

    __tablename__ = "content_chunks"
    __table_args__ = (UniqueConstraint("source_revision_id", "chunk_index"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    source_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_revisions.id"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    text_content: Mapped[str] = mapped_column(Text)
    # Start and end lines (1-based, inclusive) and, in Markdown, the heading.
    locator: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    token_count: Mapped[int] = mapped_column(Integer)
    # From a test, fixture or mock (by its path): left out of search and vectors by default.
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", index=True)
    # Model its vector was computed with; empty if it has none yet.
    embedding_model: Mapped[str | None] = mapped_column(String(200), index=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)


class CodeSymbol(Base):
    """A revision's function, method or class (tree-sitter). Derived, like the chunks.

    Earlier revisions keep their symbols: comparing their fingerprints tells whether the code
    backing a rule really changed or only its formatting.
    """

    __tablename__ = "code_symbols"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    source_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_revisions.id"), index=True
    )
    language: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(300), index=True)
    qualified_name: Mapped[str] = mapped_column(String(1000))
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    logic_hash: Mapped[str] = mapped_column(String(64), index=True)
    calls: Mapped[list[str]] = mapped_column(JSON, default=list)
    structure_version: Mapped[str] = mapped_column(String(20))


class CodeEdge(Base):
    """A call from one function to another of the same project (stage 3, phase B). Derived:
    rebuilt for the whole project after each sync, from the current revisions' symbols.

    `to_symbol_id` is empty when the name could not be resolved; `status` says why: `external`
    (imported from a package), `ambiguous` (several functions share the name) or `unknown`.
    """

    __tablename__ = "code_edges"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    from_symbol_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("code_symbols.id", ondelete="CASCADE"), index=True
    )
    to_symbol_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("code_symbols.id", ondelete="CASCADE"), index=True
    )
    callee: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))


class ExtractionTask(Base):
    """A candidate piece of code in the rule-extraction queue.

    `unit_key` is the fingerprint of path, function and content: if it was processed and has not
    changed, it is not sent to the model again. Derived: deleting it only forces repeating work.
    """

    __tablename__ = "extraction_tasks"
    __table_args__ = (UniqueConstraint("project_id", "unit_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    source_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_revisions.id"), index=True
    )
    unit_key: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(1000))
    symbol: Mapped[str | None] = mapped_column(String(1000))
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer)
    # pending, done or failed
    status: Mapped[str] = mapped_column(String(20), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    rules_found: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)


class AnalysisRun(Base):
    """A question or an analysis (extraction, diff, SQL, releases, bug) and its result.

    `result` keeps references to the citations (chunk, revision, path, lines, commit), not the
    cited text: if the source is deleted, the history keeps no copies of its content.
    """

    __tablename__ = "analysis_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    analysis_type: Mapped[AnalysisType] = mapped_column(_enum(AnalysisType, "analysis_type"))
    # The question, or a short description of the analysis (a diff is not copied whole), with
    # recognizable secrets redacted. `input_hash` is of the original text.
    question: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(String(64))
    # Commits, tags or releases compared or analyzed, when it applies.
    base_revision: Mapped[str | None] = mapped_column(String(200))
    target_revision: Mapped[str | None] = mapped_column(String(200))
    release_ref: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[AnalysisStatus] = mapped_column(
        _enum(AnalysisStatus, "analysis_status"), default=AnalysisStatus.QUEUED
    )
    answer_status: Mapped[AnswerStatus | None] = mapped_column(_enum(AnswerStatus, "answer_status"))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model_provider: Mapped[str | None] = mapped_column(String(100))
    model_name: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(20))
    retrieval_version: Mapped[str] = mapped_column(String(20))
    error_summary: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


# Knowledge model. Each row carries `workspace_id` and `project_id`, and the
# foreign keys between these tables include both: a rule of one project cannot cite,
# relate to or enter the snapshot of another. What the LLM generates is born unverified.


class ProjectScoped:
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)


def _scoped_fk(column: str, table: str, key: str = "id") -> ForeignKeyConstraint:
    """`column` points to a row of `table` in the same workspace and project."""
    return ForeignKeyConstraint(
        [column, "workspace_id", "project_id"],
        [f"{table}.{key}", f"{table}.workspace_id", f"{table}.project_id"],
    )


def _scope_key(key: str = "id") -> UniqueConstraint:
    return UniqueConstraint(key, "workspace_id", "project_id")


class KnowledgeEntity(ProjectScoped, Timestamps, Base):
    """Identity of a concept, independent of its versions."""

    __tablename__ = "knowledge_entities"
    __table_args__ = (UniqueConstraint("project_id", "entity_type", "canonical_key"), _scope_key())

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[EntityType] = mapped_column(_enum(EntityType, "entity_type"))
    canonical_key: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    lifecycle_status: Mapped[EntityLifecycle] = mapped_column(
        _enum(EntityLifecycle, "entity_lifecycle"), default=EntityLifecycle.PROPOSED
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    # Why it must be reviewed (for example, a source that backed it was deleted). Cleared
    # when a person confirms or rejects its current version.
    review_reason: Mapped[str | None] = mapped_column(String(64))


class KnowledgeEntityVersion(ProjectScoped, Base):
    """Version of an entity. The content never changes (trigger); only its review does."""

    __tablename__ = "knowledge_entity_versions"
    __table_args__ = (
        _scoped_fk("knowledge_entity_id", "knowledge_entities"),
        UniqueConstraint("knowledge_entity_id", "version_number"),
        UniqueConstraint("id", "knowledge_entity_id"),
        _scope_key(),
        CheckConstraint("version_number >= 1", name="positive_version"),
        CheckConstraint("length(content_hash) = 64", name="content_hash_sha256"),
        # Confirming or rejecting requires a recorded review.
        CheckConstraint(
            "verification_status NOT IN ('human_verified', 'rejected') OR reviewed_at IS NOT NULL",
            name="review_recorded",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    knowledge_entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    # Empty when the entity has a typed extension table: the content lives there.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    origin: Mapped[VersionOrigin] = mapped_column(_enum(VersionOrigin, "version_origin"))
    model_provider: Mapped[str | None] = mapped_column(String(100))
    model_name: Mapped[str | None] = mapped_column(String(200))
    extractor_version: Mapped[str | None] = mapped_column(String(50))
    verification_status: Mapped[VerificationStatus] = mapped_column(
        _enum(VerificationStatus, "verification_status"), default=VerificationStatus.UNVERIFIED
    )
    created_by: Mapped[str | None] = mapped_column(String(200))
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class BusinessRule(ProjectScoped, Timestamps, Base):
    __tablename__ = "business_rules"
    __table_args__ = (_scoped_fk("knowledge_entity_id", "knowledge_entities"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    knowledge_entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    rule_category: Mapped[RuleCategory] = mapped_column(_enum(RuleCategory, "rule_category"))
    owner_ref: Mapped[str | None] = mapped_column(String(200))
    criticality: Mapped[Criticality] = mapped_column(
        _enum(Criticality, "criticality"), default=Criticality.MEDIUM
    )


class BusinessRuleVersion(ProjectScoped, Base):
    """Typed extension of a rule version. JSON lists are validated on write."""

    __tablename__ = "business_rule_versions"
    __table_args__ = (_scoped_fk("knowledge_entity_version_id", "knowledge_entity_versions"),)

    knowledge_entity_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    statement: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    preconditions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    trigger_conditions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    expected_behavior: Mapped[list[Any]] = mapped_column(JSON, default=list)
    exceptions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    impact_targets: Mapped[list[Any]] = mapped_column(JSON, default=list)
    supersedes_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_entity_versions.id")
    )
    # The only modifiable column: cleared if the question it came from is deleted.
    created_from_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_runs.id")
    )


class EvidenceRecord(ProjectScoped, Base):
    """Verifiable reference to a specific revision, with lines and an excerpt."""

    __tablename__ = "evidence_records"
    __table_args__ = (
        _scoped_fk("target_entity_id", "knowledge_entities"),
        # The version, if given, belongs to the same entity.
        ForeignKeyConstraint(
            ["target_version_id", "target_entity_id"],
            ["knowledge_entity_versions.id", "knowledge_entity_versions.knowledge_entity_id"],
        ),
        _scope_key(),
        CheckConstraint(
            "source_revision_id IS NOT NULL OR evidence_type = 'human_review'", name="has_source"
        ),
        CheckConstraint("(excerpt IS NULL) = (excerpt_hash IS NULL)", name="excerpt_hashed"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    target_entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    target_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    source_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_revisions.id"), index=True
    )
    evidence_role: Mapped[EvidenceRole] = mapped_column(_enum(EvidenceRole, "evidence_role"))
    evidence_type: Mapped[EvidenceType] = mapped_column(_enum(EvidenceType, "evidence_type"))
    # Path, lines (1-based, inclusive) and commit of the revision.
    locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    excerpt: Mapped[str | None] = mapped_column(Text)
    excerpt_hash: Mapped[str | None] = mapped_column(String(64))
    verification_method: Mapped[VerificationMethod] = mapped_column(
        _enum(VerificationMethod, "verification_method")
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        _enum(VerificationStatus, "verification_status"), default=VerificationStatus.UNVERIFIED
    )
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)


class EntityRelationship(ProjectScoped, Base):
    __tablename__ = "entity_relationships"
    __table_args__ = (
        _scoped_fk("from_entity_id", "knowledge_entities"),
        _scoped_fk("to_entity_id", "knowledge_entities"),
        _scoped_fk("evidence_record_id", "evidence_records"),
        UniqueConstraint("from_entity_id", "relation_type", "to_entity_id"),
        CheckConstraint("from_entity_id <> to_entity_id", name="not_self"),
        CheckConstraint(
            "valid_from IS NULL OR valid_until IS NULL OR valid_until > valid_from",
            name="valid_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    from_entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    relation_type: Mapped[RelationType] = mapped_column(_enum(RelationType, "relation_type"))
    to_entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    evidence_record_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Why Scrinium related them from the code (stage 3, phase C), like "same_function",
    # "calls:2" or "constant:MAX_LIVES"; empty for a relationship a person created. The ones
    # with a basis are rebuilt after each extraction.
    basis: Mapped[str | None] = mapped_column(String(200))
    valid_from: Mapped[datetime | None] = mapped_column(UtcDateTime)
    valid_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)


class Finding(ProjectScoped, Base):
    """A possible problem or gap. Born as a hypothesis; only a review confirms it."""

    __tablename__ = "findings"
    __table_args__ = (
        _scope_key(),
        CheckConstraint(
            "assessment_status NOT IN ('human_confirmed', 'false_positive') "
            "OR reviewed_at IS NOT NULL",
            name="review_recorded",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    finding_type: Mapped[FindingType] = mapped_column(_enum(FindingType, "finding_type"))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[Severity] = mapped_column(_enum(Severity, "severity"))
    assessment_status: Mapped[AssessmentStatus] = mapped_column(
        _enum(AssessmentStatus, "assessment_status"), default=AssessmentStatus.HYPOTHESIS
    )
    rule_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("business_rule_versions.knowledge_entity_version_id")
    )
    location: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    recommended_next_step: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    reviewed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class FindingEvidence(ProjectScoped, Base):
    __tablename__ = "finding_evidence"
    __table_args__ = (
        _scoped_fk("finding_id", "findings"),
        _scoped_fk("evidence_record_id", "evidence_records"),
    )

    finding_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    evidence_record_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, index=True)
    relevance: Mapped[EvidenceRelevance] = mapped_column(
        _enum(EvidenceRelevance, "evidence_relevance")
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)


class SoftwareRelease(ProjectScoped, Base):
    __tablename__ = "software_releases"
    __table_args__ = (
        UniqueConstraint("source_connection_id", "repository_key", "release_name"),
        _scope_key(),
        _scoped_fk("previous_release_id", "software_releases"),
        CheckConstraint("previous_release_id <> id", name="not_own_previous"),
        CheckConstraint(
            "commit_sha IS NULL OR length(commit_sha) IN (40, 64)", name="commit_sha_length"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_connections.id"), index=True
    )
    repository_key: Mapped[str] = mapped_column(String(500))
    release_name: Mapped[str] = mapped_column(String(200))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    previous_release_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    released_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    captured_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    status: Mapped[ReleaseStatus] = mapped_column(
        _enum(ReleaseStatus, "release_status"), default=ReleaseStatus.DISCOVERED
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)


class ReleaseKnowledgeSnapshot(ProjectScoped, Base):
    """Which version of each entity, and which relationships, applied to a release. Immutable.

    The plan puts the entity and the version in the same snapshot row; here they go in
    `release_snapshot_entries`, one row per entity, so each has its foreign key.
    """

    __tablename__ = "release_knowledge_snapshots"
    __table_args__ = (
        _scoped_fk("software_release_id", "software_releases"),
        UniqueConstraint("software_release_id", "snapshot_version"),
        _scope_key(),
        CheckConstraint("length(snapshot_hash) = 64", name="snapshot_hash_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    software_release_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    snapshot_version: Mapped[int] = mapped_column(Integer)
    # Copy of the current relationships (from, type, to): relationships are not versioned.
    relationship_snapshot: Mapped[list[Any]] = mapped_column(JSON, default=list)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(50))
    retrieval_schema_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)


class ReleaseSnapshotEntry(ProjectScoped, Base):
    __tablename__ = "release_snapshot_entries"
    __table_args__ = (
        _scoped_fk("snapshot_id", "release_knowledge_snapshots"),
        _scoped_fk("knowledge_entity_id", "knowledge_entities"),
        ForeignKeyConstraint(
            ["entity_version_id", "knowledge_entity_id"],
            ["knowledge_entity_versions.id", "knowledge_entity_versions.knowledge_entity_id"],
        ),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    knowledge_entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    entity_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)


class FlowDefinition(ProjectScoped, Timestamps, Base):
    """Identity of a flow. Name, key, summary and lifecycle live on the entity."""

    __tablename__ = "flow_definitions"
    __table_args__ = (_scoped_fk("knowledge_entity_id", "knowledge_entities"), _scope_key())

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    knowledge_entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    flow_type: Mapped[FlowType] = mapped_column(_enum(FlowType, "flow_type"))


class FlowVersion(ProjectScoped, Base):
    __tablename__ = "flow_versions"
    __table_args__ = (
        _scoped_fk("knowledge_entity_version_id", "knowledge_entity_versions"),
        _scoped_fk("flow_definition_id", "flow_definitions"),
        _scope_key("knowledge_entity_version_id"),
    )

    knowledge_entity_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    flow_definition_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    entry_conditions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    success_conditions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    failure_conditions: Mapped[list[Any]] = mapped_column(JSON, default=list)


class FlowStep(ProjectScoped, Base):
    __tablename__ = "flow_steps"
    __table_args__ = (
        _scoped_fk("flow_version_id", "flow_versions", "knowledge_entity_version_id"),
        _scoped_fk("knowledge_entity_id", "knowledge_entities"),
        UniqueConstraint("flow_version_id", "step_key"),
        UniqueConstraint("flow_version_id", "position"),
        UniqueConstraint("id", "flow_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    flow_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    step_key: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[FlowStepType] = mapped_column(_enum(FlowStepType, "flow_step_type"))
    label: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    knowledge_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Declarative description. Never evaluated.
    condition_expression: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)


class FlowTransition(ProjectScoped, Base):
    __tablename__ = "flow_transitions"
    __table_args__ = (
        _scoped_fk("flow_version_id", "flow_versions", "knowledge_entity_version_id"),
        # Both steps belong to the same flow version.
        ForeignKeyConstraint(
            ["from_step_id", "flow_version_id"], ["flow_steps.id", "flow_steps.flow_version_id"]
        ),
        ForeignKeyConstraint(
            ["to_step_id", "flow_version_id"], ["flow_steps.id", "flow_steps.flow_version_id"]
        ),
        _scoped_fk("evidence_record_id", "evidence_records"),
        UniqueConstraint("flow_version_id", "from_step_id", "to_step_id", "transition_type"),
        CheckConstraint(
            "transition_type <> 'conditional' OR condition_description IS NOT NULL",
            name="conditional_has_condition",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    flow_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    from_step_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    to_step_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    transition_type: Mapped[TransitionType] = mapped_column(
        _enum(TransitionType, "transition_type")
    )
    condition_description: Mapped[str | None] = mapped_column(String(1000))
    evidence_record_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
