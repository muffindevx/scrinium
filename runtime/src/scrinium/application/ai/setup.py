"""Where the two models live (ADR-013): the answer model and the search model.

The **answer model** writes rules and answers questions. Before anything is sent, a person
chooses one of these:

- **provider**: their own provider (DeepSeek, GLM, Claude or any OpenAI-compatible API),
  recommended. Choosing it sends nothing: the provider is registered and each project consents
  to it as before (ADR-010).
- **local_server**: a server on this machine that speaks the OpenAI API (llama-server, Ollama,
  vLLM), at a loopback address. It becomes the local model every project uses by default.
- **bundled** (the default): the small answer model installed with Scrinium, for trying it.

The **search model** turns text into vectors for search by meaning: the bundled one (default),
a local server that serves embeddings, or a provider. A provider for search receives the indexed
text of every project, so choosing it is a workspace decision the console confirms first.

**Isolated mode** is separate from both: a flag with which Scrinium itself refuses providers
(no network rule, the same with Docker or installed). No provider can be
registered, consented to or used, for either model, and `security-status` says so. Turning it on
revokes every consent in force, and a provider chosen for either model is dropped (both go back
to the included models). The providers and their keys stay.

An organisation can force isolated mode with `SCRINIUM_PROFILE=isolated`: then the choice
cannot be changed from the console, and `security-status` reports it as managed.

The choice lives in the workspace's settings (`Workspace.settings["ai_setup"]`), so it needs no
schema change and travels with the backup.
"""

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.workspaces import local_workspace, record_audit
from scrinium.domain.enums import AiMode, AuditResult
from scrinium.infrastructure.local_http import check_loopback_url
from scrinium.infrastructure.sqlite.models import AiConsent, Project, utc_now

PROFILE_ENV: Final = "SCRINIUM_PROFILE"
ISOLATED_PROFILE: Final = "isolated"
_KEY: Final = "ai_setup"


class SetupChoice(StrEnum):
    """The answer model. ISOLATED is still accepted on input: it turns isolated mode on."""

    PROVIDER = "provider"
    LOCAL_SERVER = "local_server"
    BUNDLED = "bundled"
    ISOLATED = "isolated"


class SearchKind(StrEnum):
    BUNDLED = "bundled"
    LOCAL_SERVER = "local_server"
    PROVIDER = "provider"


class SetupError(ValueError):
    """A first-run choice Scrinium refuses. `code` is stable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LocalServer:
    url: str
    model: str


@dataclass(frozen=True)
class SearchSetup:
    kind: SearchKind
    url: str | None = None
    model: str | None = None
    provider_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "url": self.url,
            "model": self.model,
            "provider_id": self.provider_id,
        }


BUNDLED_SEARCH: Final = SearchSetup(SearchKind.BUNDLED)


@dataclass(frozen=True)
class AiSetup:
    # The answer model: BUNDLED until a person chooses another. Never ISOLATED: that is
    # `isolated`. Typed as optional for callers that build one by hand.
    choice: SetupChoice | None
    local_server: LocalServer | None
    # Forced by the environment: the console cannot change it.
    managed: bool
    isolated: bool = False
    search: SearchSetup = BUNDLED_SEARCH

    def as_dict(self) -> dict[str, Any]:
        return {
            "choice": self.choice.value if self.choice else None,
            "isolated": self.isolated,
            "managed": self.managed,
            "local_server": (
                {"url": self.local_server.url, "model": self.local_server.model}
                if self.local_server
                else None
            ),
            "search": self.search.as_dict(),
        }


def managed_isolated() -> bool:
    return os.environ.get(PROFILE_ENV, "").strip().lower() == ISOLATED_PROFILE


def _stored(session: Session) -> dict[str, Any]:
    return dict(dict(local_workspace(session).settings or {}).get(_KEY) or {})


def _search(stored: dict[str, Any]) -> SearchSetup:
    raw = stored.get("search")
    if not isinstance(raw, dict) or raw.get("kind") not in set(SearchKind):
        return BUNDLED_SEARCH
    return SearchSetup(
        SearchKind(raw["kind"]), raw.get("url"), raw.get("model"), raw.get("provider_id")
    )


def read_setup(session: Session) -> AiSetup:
    stored = _stored(session)
    raw = stored.get("choice")
    # Before the two models, isolated mode was one of the answer choices.
    isolated = bool(stored.get("isolated")) or raw == SetupChoice.ISOLATED.value
    # Nothing chosen: the included answer model, installed with Scrinium. Nothing leaves.
    choice = (
        SetupChoice(raw)
        if raw in set(SetupChoice) - {SetupChoice.ISOLATED}
        else SetupChoice.BUNDLED
    )
    server = stored.get("local_server")
    local = (
        LocalServer(str(server["url"]), str(server["model"]))
        if choice is SetupChoice.LOCAL_SERVER and isinstance(server, dict)
        else None
    )
    search = _search(stored)
    if managed_isolated():
        if choice is SetupChoice.PROVIDER:
            choice = SetupChoice.BUNDLED
        if search.kind is SearchKind.PROVIDER:
            search = BUNDLED_SEARCH
        return AiSetup(choice, local, managed=True, isolated=True, search=search)
    return AiSetup(choice, local, managed=False, isolated=isolated, search=search)


def is_isolated(session: Session) -> bool:
    return read_setup(session).isolated


def require_not_isolated(session: Session) -> None:
    """Raises `SetupError("isolated_mode")`: nothing may be registered or consented to."""
    if is_isolated(session):
        raise SetupError("isolated_mode")


def clean_local_server(url: str, model: str) -> LocalServer:
    try:
        clean = check_loopback_url(url.strip())
    except ValueError:
        raise SetupError("local_server_not_loopback") from None
    model = model.strip()
    if not 1 <= len(model) <= 200 or any(c.isspace() for c in model):
        raise SetupError("invalid_model")
    return LocalServer(clean, model)


def _save(session: Session, entry: dict[str, Any]) -> None:
    workspace = local_workspace(session)
    workspace.settings = {**dict(workspace.settings or {}), _KEY: entry}


def choose(
    session: Session,
    choice: SetupChoice,
    local_server: LocalServer | None = None,
    request_id: str | None = None,
) -> tuple[AiSetup, int]:
    """Records the answer model (or, with ISOLATED, turns isolated mode on).

    Returns the setup and how many consents isolation revoked."""
    if choice is SetupChoice.ISOLATED:
        return set_isolated(session, True, request_id)
    if managed_isolated() and choice is SetupChoice.PROVIDER:
        raise SetupError("isolated_managed")
    if choice is SetupChoice.PROVIDER and read_setup(session).isolated:
        raise SetupError("isolated_mode")
    if choice is SetupChoice.LOCAL_SERVER and local_server is None:
        raise SetupError("local_server_required")
    workspace = local_workspace(session)
    entry = _stored(session)
    if entry.get("choice") == SetupChoice.ISOLATED.value:
        entry["isolated"] = True  # the old way of saying it: kept as a flag
    entry.pop("local_server", None)
    entry.update(choice=choice.value, chosen_at=utc_now().isoformat())
    if choice is SetupChoice.LOCAL_SERVER and local_server is not None:
        entry["local_server"] = {"url": local_server.url, "model": local_server.model}
    _save(session, entry)
    record_audit(
        session, workspace.id, "ai_setup.choose", "workspace", str(workspace.id),
        AuditResult.SUCCESS, request_id,
        {"choice": choice.value, **({"local_server": local_server.url} if local_server else {})},
    )  # fmt: skip
    session.flush()
    return read_setup(session), 0


def set_isolated(session: Session, on: bool, request_id: str | None = None) -> tuple[AiSetup, int]:
    """Turns isolated mode on (revoking every consent and dropping a provider chosen for either
    model) or off (consents stay revoked: each project gives them again)."""
    if managed_isolated():
        raise SetupError("isolated_managed")
    workspace = local_workspace(session)
    entry = _stored(session)
    revoked = 0
    if on:
        revoked = _revoke_everything(session, request_id)
        if entry.get("choice") in (SetupChoice.PROVIDER.value, SetupChoice.ISOLATED.value):
            entry.pop("choice", None)
        if _search(entry).kind is SearchKind.PROVIDER:
            entry["search"] = BUNDLED_SEARCH.as_dict()
    elif entry.get("choice") == SetupChoice.ISOLATED.value:
        entry.pop("choice", None)
    entry["isolated"] = on
    _save(session, entry)
    record_audit(
        session, workspace.id, "ai_setup.isolated", "workspace", str(workspace.id),
        AuditResult.SUCCESS, request_id, {"on": on, "consents_revoked": revoked},
    )  # fmt: skip
    session.flush()
    return read_setup(session), revoked


def isolation_effects(session: Session) -> dict[str, Any]:
    """What turning isolated mode on would do, said before it is done."""
    setup = read_setup(session)
    consents = list(session.scalars(select(AiConsent).where(AiConsent.revoked_at.is_(None))))
    projects = sorted(
        {
            project.name
            for project in session.scalars(
                select(Project).where(Project.id.in_({c.project_id for c in consents}))
            )
        }
    )
    return {
        "answer_model_lost": setup.choice is SetupChoice.PROVIDER,
        "search_model_reset": setup.search.kind is SearchKind.PROVIDER,
        "consents": len(consents),
        "projects": projects,
    }


def choose_search(session: Session, search: SearchSetup, request_id: str | None = None) -> AiSetup:
    """Records the search model. A provider is refused in isolated mode."""
    setup = read_setup(session)
    if search.kind is SearchKind.PROVIDER and setup.isolated:
        raise SetupError("isolated_managed" if setup.managed else "isolated_mode")
    if search.kind is SearchKind.LOCAL_SERVER:
        server = clean_local_server(search.url or "", search.model or "")
        search = SearchSetup(SearchKind.LOCAL_SERVER, server.url, server.model)
    elif search.kind is SearchKind.PROVIDER:
        model = (search.model or "").strip()
        if not search.provider_id or not 1 <= len(model) <= 200:
            raise SetupError("invalid_model")
        search = SearchSetup(SearchKind.PROVIDER, None, model, search.provider_id)
    else:
        search = BUNDLED_SEARCH
    workspace = local_workspace(session)
    entry = _stored(session)
    entry["search"] = search.as_dict()
    _save(session, entry)
    record_audit(
        session, workspace.id, "ai_setup.search", "workspace", str(workspace.id),
        AuditResult.SUCCESS, request_id,
        {"kind": search.kind.value, "url": search.url, "model": search.model,
         "provider_id": search.provider_id},
    )  # fmt: skip
    session.flush()
    return read_setup(session)


def _revoke_everything(session: Session, request_id: str | None) -> int:
    consents = list(session.scalars(select(AiConsent).where(AiConsent.revoked_at.is_(None))))
    now = utc_now()
    for consent in consents:
        consent.revoked_at = now
        record_audit(
            session, consent.workspace_id, "ai_consent.revoke", "project",
            str(consent.project_id), AuditResult.SUCCESS, request_id,
            {"host": consent.host, "model": consent.model, "reason": "isolated_mode"},
        )  # fmt: skip
    for project in session.scalars(select(Project)):
        project.ai_mode = AiMode.LOCAL_ONLY
        project.task_models = {}
    session.flush()
    return len(consents)
