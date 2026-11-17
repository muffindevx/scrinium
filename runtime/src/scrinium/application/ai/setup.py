"""The first-run choice of where the writing model lives (ADR-013).

Before anything is sent, a person chooses one of three:

- **provider**: their own provider (DeepSeek, GLM, Claude or any OpenAI-compatible API),
  recommended. Choosing it sends nothing: the provider is registered and each project consents
  to it as before (ADR-010).
- **local_server**: a server on this machine that speaks the OpenAI API (llama-server, Ollama,
  vLLM), at a loopback address. It becomes the local model every project uses by default.
- **isolated**: a profile that blocks every egress. No provider can be registered, consented to
  or used, and `security-status` says so. Choosing it revokes every consent in force.

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
    PROVIDER = "provider"
    LOCAL_SERVER = "local_server"
    ISOLATED = "isolated"


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
class AiSetup:
    # None until a person chooses (or the profile is forced).
    choice: SetupChoice | None
    local_server: LocalServer | None
    # Forced by the environment: the console cannot change it.
    managed: bool

    @property
    def isolated(self) -> bool:
        return self.choice is SetupChoice.ISOLATED

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
        }


def managed_isolated() -> bool:
    return os.environ.get(PROFILE_ENV, "").strip().lower() == ISOLATED_PROFILE


def read_setup(session: Session) -> AiSetup:
    if managed_isolated():
        return AiSetup(SetupChoice.ISOLATED, None, managed=True)
    stored = dict(local_workspace(session).settings or {}).get(_KEY) or {}
    raw = stored.get("choice")
    choice = SetupChoice(raw) if raw in set(SetupChoice) else None
    server = stored.get("local_server")
    local = (
        LocalServer(str(server["url"]), str(server["model"]))
        if choice is SetupChoice.LOCAL_SERVER and isinstance(server, dict)
        else None
    )
    return AiSetup(choice, local, managed=False)


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


def choose(
    session: Session,
    choice: SetupChoice,
    local_server: LocalServer | None = None,
    request_id: str | None = None,
) -> tuple[AiSetup, int]:
    """Records the choice. Returns the setup and how many consents isolation revoked."""
    if managed_isolated():
        raise SetupError("isolated_managed")
    if choice is SetupChoice.LOCAL_SERVER and local_server is None:
        raise SetupError("local_server_required")
    workspace = local_workspace(session)
    revoked = 0
    if choice is SetupChoice.ISOLATED:
        revoked = _revoke_everything(session, request_id)
    entry: dict[str, Any] = {"choice": choice.value, "chosen_at": utc_now().isoformat()}
    if choice is SetupChoice.LOCAL_SERVER and local_server is not None:
        entry["local_server"] = {"url": local_server.url, "model": local_server.model}
    workspace.settings = {**dict(workspace.settings or {}), _KEY: entry}
    record_audit(
        session, workspace.id, "ai_setup.choose", "workspace", str(workspace.id),
        AuditResult.SUCCESS, request_id,
        {"choice": choice.value, "consents_revoked": revoked,
         **({"local_server": local_server.url} if local_server else {})},
    )  # fmt: skip
    session.flush()
    return read_setup(session), revoked


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
