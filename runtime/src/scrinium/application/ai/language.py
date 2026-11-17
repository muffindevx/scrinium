"""The workspace's language: the console's and the model's output (phase 5.5).

One setting for both, in `Workspace.settings["language"]`: Spanish (default) or English. The
prompts are written once, in Spanish; for English output the sentence that fixes the language
is rewritten when the request is sent (`localize`), so every task follows it from one place and
the system prompt stays byte-identical within a language (the server's prompt cache).
"""

import re
from typing import Final

from sqlalchemy.orm import Session

from scrinium.application.workspaces import local_workspace, record_audit
from scrinium.domain.enums import AuditResult
from scrinium.infrastructure.llm.base import ChatMessage

LANGUAGES: Final = ("es", "en")
DEFAULT_LANGUAGE: Final = "es"
_KEY: Final = "language"
# "en espanol", "en español": what the prompts say about the language of the answer.
_SPANISH: Final = re.compile(r"\ben espa(?:n|ñ)ol\b")
_ENGLISH: Final = "en ingles (English)"


def read_language(session: Session) -> str:
    stored = dict(local_workspace(session).settings or {}).get(_KEY)
    return stored if stored in LANGUAGES else DEFAULT_LANGUAGE


def set_language(session: Session, language: str, request_id: str | None = None) -> str:
    if language not in LANGUAGES:
        raise ValueError("unsupported language")
    workspace = local_workspace(session)
    workspace.settings = {**dict(workspace.settings or {}), _KEY: language}
    record_audit(
        session, workspace.id, "workspace.language", "workspace", str(workspace.id),
        AuditResult.SUCCESS, request_id, {"language": language},
    )  # fmt: skip
    session.flush()
    return language


def localize(messages: list[ChatMessage], language: str) -> list[ChatMessage]:
    """The system prompts asking for `language`. Only the system messages change: the user
    message carries the repository's text, which is never rewritten."""
    if language == DEFAULT_LANGUAGE:
        return messages
    return [
        ChatMessage(m.role, _SPANISH.sub(_ENGLISH, m.content)) if m.role == "system" else m
        for m in messages
    ]
