"""Revision chunks and the FTS5 full-text search index.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-07 16:52:11.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# FTS5 keeps its own copy of the text: it is a derived index and can be rebuilt.
# `chunk_id` is an indexed column so the delete trigger finds it with MATCH instead of
# scanning the table; the person's searches are limited to `path` and `body`.
# remove_diacritics 2: "camion" finds "camión". SQLite syntax.


def upgrade() -> None:
    op.create_table(
        "content_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_revision_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_revision_id"],
            ["source_revisions.id"],
            name="fk_content_chunks_source_revision_id_source_revisions",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_content_chunks_workspace_id_workspaces"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_content_chunks"),
        sa.UniqueConstraint(
            "source_revision_id",
            "chunk_index",
            name="uq_content_chunks_source_revision_id_chunk_index",
        ),
    )
    op.create_index("ix_content_chunks_embedding_model", "content_chunks", ["embedding_model"])
    op.create_index(
        "ix_content_chunks_source_revision_id", "content_chunks", ["source_revision_id"]
    )
    op.create_index("ix_content_chunks_workspace_id", "content_chunks", ["workspace_id"])

    op.execute(
        "CREATE VIRTUAL TABLE content_chunks_fts USING fts5("
        "chunk_id, path, body, tokenize = 'unicode61 remove_diacritics 2')"
    )
    op.execute(
        "CREATE TRIGGER content_chunks_fts_insert AFTER INSERT ON content_chunks BEGIN "
        "INSERT INTO content_chunks_fts (chunk_id, path, body) VALUES (new.id, "
        "(SELECT json_extract(metadata, '$.path') FROM source_revisions "
        "WHERE id = new.source_revision_id), new.text_content); END"
    )
    op.execute(
        "CREATE TRIGGER content_chunks_fts_delete AFTER DELETE ON content_chunks BEGIN "
        "DELETE FROM content_chunks_fts WHERE content_chunks_fts MATCH "
        "'chunk_id:\"' || old.id || '\"'; END"
    )
    # A chunk never changes: if the revision changes, new chunks are created.
    op.execute(
        "CREATE TRIGGER content_chunks_text_no_update BEFORE UPDATE OF text_content, "
        "source_revision_id, chunk_index, locator ON content_chunks "
        "BEGIN SELECT RAISE(ABORT, 'content_chunks: el texto es de solo anadir'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER content_chunks_text_no_update")
    op.execute("DROP TRIGGER content_chunks_fts_delete")
    op.execute("DROP TRIGGER content_chunks_fts_insert")
    op.execute("DROP TABLE content_chunks_fts")
    op.drop_index("ix_content_chunks_workspace_id", "content_chunks")
    op.drop_index("ix_content_chunks_source_revision_id", "content_chunks")
    op.drop_index("ix_content_chunks_embedding_model", "content_chunks")
    op.drop_table("content_chunks")
