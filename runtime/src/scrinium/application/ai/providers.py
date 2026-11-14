"""Remote model providers and per-project consent to use them (ADR-010).

- A provider is registered once, with its API key. The key goes to the OS keyring
  (`SecretStore`, ADR-006); the database keeps an opaque reference, the provider's host and the
  key's last four characters. Without a secure keyring nothing is registered.
- A key is only ever handed out for the host it was registered with (`provider_key`).
- A project sends nothing until a person consents for it: a provider, a model, the tasks, and
  the retention notice they read. Revoking returns the project to `local_only`.
"""

import ipaddress
import uuid
from datetime import date
from typing import Any, Final
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scrinium.application.ai.retention import notice_for
from scrinium.application.ingestion.policy import validate_user_patterns
from scrinium.application.workspaces import local_workspace, record_audit, reviewer_name
from scrinium.domain.enums import AiMode, AiTask, AuditResult, ProviderKind
from scrinium.infrastructure.secret_store import SecretStore, SecretStoreUnavailableError
from scrinium.infrastructure.sqlite.models import (
    AiCacheEntry,
    AiConsent,
    AiRequest,
    ModelProvider,
    Project,
    ProviderModel,
    utc_now,
)

MIN_KEY_LENGTH: Final = 8
MAX_KEY_LENGTH: Final = 4096


class ProviderError(ValueError):
    """A request about providers or consent that Scrinium refuses. `code` is stable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def check_base_url(url: str) -> tuple[str, str]:
    """`(base URL without trailing slash, host)`. HTTPS to a named host only.

    No user or password in the URL, no query or fragment, and no IP address: a provider is a
    named service. A machine on this network is a different case, not covered (ADR-010).
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        raise ProviderError("invalid_base_url")
    if parts.query or parts.fragment or "." not in host:
        raise ProviderError("invalid_base_url")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise ProviderError("invalid_base_url")
    return f"{parts.scheme}://{parts.netloc.lower()}{parts.path.rstrip('/')}", host


def _clean_key(api_key: str) -> str:
    key = api_key.strip()
    if not MIN_KEY_LENGTH <= len(key) <= MAX_KEY_LENGTH or any(c.isspace() for c in key):
        raise ProviderError("invalid_api_key")
    return key


def describe(provider: ModelProvider) -> dict[str, Any]:
    """What the UI may know about a provider. Never the key or its reference."""
    return {
        "id": str(provider.id), "name": provider.name, "kind": provider.kind.value,
        "base_url": provider.base_url, "host": provider.host,
        "has_key": provider.secret_ref is not None, "key_hint": provider.key_hint,
        "created_at": provider.created_at.isoformat(),
    }  # fmt: skip


def list_providers(session: Session) -> list[dict[str, Any]]:
    """Each provider with the models of it this workspace uses."""
    models: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for row in session.scalars(select(ProviderModel).order_by(ProviderModel.model)):
        models.setdefault(row.provider_id, []).append({"id": str(row.id), "model": row.model})
    return [
        {**describe(provider), "models": models.get(provider.id, [])}
        for provider in session.scalars(select(ModelProvider).order_by(ModelProvider.name))
    ]


def _clean_model(model: str) -> str:
    model = model.strip()
    if not 1 <= len(model) <= 200 or any(c.isspace() for c in model):
        raise ProviderError("invalid_model")
    return model


def add_model(
    session: Session, provider_id: uuid.UUID, model: str, request_id: str | None = None
) -> ProviderModel:
    """A model of this provider, added to the catalog. Nothing is sent."""
    provider = session.get(ModelProvider, provider_id)
    if provider is None:
        raise LookupError
    model = _clean_model(model)
    existing = session.scalars(
        select(ProviderModel).where(
            ProviderModel.provider_id == provider.id, ProviderModel.model == model
        )
    ).first()
    if existing is not None:
        return existing
    row = ProviderModel(workspace_id=provider.workspace_id, provider_id=provider.id, model=model)
    session.add(row)
    session.flush()
    record_audit(
        session, provider.workspace_id, "provider_model.add", "model_provider", str(provider.id),
        AuditResult.SUCCESS, request_id, {"model": model},
    )  # fmt: skip
    return row


def remove_model(
    session: Session, provider_id: uuid.UUID, model_id: uuid.UUID, request_id: str | None = None
) -> int:
    """Out of the catalog; the consents that named it are revoked. Returns how many."""
    row = session.get(ProviderModel, model_id)
    if row is None or row.provider_id != provider_id:
        raise LookupError
    revoked = 0
    for consent in session.scalars(
        select(AiConsent).where(
            AiConsent.provider_id == provider_id, AiConsent.model == row.model,
            AiConsent.revoked_at.is_(None),
        )
    ):  # fmt: skip
        _revoke(session, consent, request_id)
        revoked += 1
    record_audit(
        session, row.workspace_id, "provider_model.remove", "model_provider", str(provider_id),
        AuditResult.SUCCESS, request_id, {"model": row.model, "consents_revoked": revoked},
    )  # fmt: skip
    session.delete(row)
    return revoked


def update_provider(
    session: Session,
    store: SecretStore,
    provider_id: uuid.UUID,
    *,
    name: str | None = None,
    kind: ProviderKind | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    request_id: str | None = None,
) -> tuple[ModelProvider, int]:
    """Changes a provider. A new host needs a new key (a key goes only to the host it was given
    for) and revokes the consents given for the old host. Returns the provider and how many
    consents were revoked."""
    provider = session.get(ModelProvider, provider_id)
    if provider is None:
        raise LookupError
    if name is not None:
        name = name.strip()
        if not 1 <= len(name) <= 100:
            raise ProviderError("invalid_name")
        taken = session.scalars(
            select(ModelProvider.id).where(
                ModelProvider.workspace_id == provider.workspace_id,
                ModelProvider.name == name, ModelProvider.id != provider.id,
            )
        ).first()  # fmt: skip
        if taken:
            raise ProviderError("name_taken")
        provider.name = name
    if kind is not None:
        provider.kind = kind
    key = _clean_key(api_key) if api_key else None
    revoked = 0
    if base_url is not None:
        url, host = check_base_url(base_url)
        if host != provider.host:
            if key is None:
                raise ProviderError("key_required_for_new_host")
            for consent in session.scalars(
                select(AiConsent).where(
                    AiConsent.provider_id == provider.id, AiConsent.revoked_at.is_(None)
                )
            ):
                _revoke(session, consent, request_id)
                revoked += 1
        provider.base_url, provider.host = url, host
    if key is not None:
        if store.backend_name is None:
            raise ProviderError("keyring_unavailable")
        try:
            new_ref = store.put(key)
            if provider.secret_ref is not None:
                store.delete(provider.secret_ref)
        except SecretStoreUnavailableError:
            raise ProviderError("keyring_unavailable") from None
        provider.secret_ref, provider.key_hint = new_ref, key[-4:]
    record_audit(
        session, provider.workspace_id, "model_provider.update", "model_provider",
        str(provider.id), AuditResult.SUCCESS, request_id,
        {"kind": provider.kind.value, "host": provider.host, "key_replaced": key is not None,
         "consents_revoked": revoked},
    )  # fmt: skip
    session.flush()
    return provider, revoked


def register_provider(
    session: Session,
    store: SecretStore,
    name: str,
    kind: ProviderKind,
    base_url: str,
    api_key: str,
    request_id: str | None = None,
) -> ModelProvider:
    """A new provider. The key is checked, put in the keyring, and never read back here."""
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise ProviderError("invalid_name")
    url, host = check_base_url(base_url)
    key = _clean_key(api_key)
    if store.backend_name is None:
        raise ProviderError("keyring_unavailable")
    workspace = local_workspace(session)
    if session.scalars(
        select(ModelProvider.id).where(
            ModelProvider.workspace_id == workspace.id, ModelProvider.name == name
        )
    ).first():
        raise ProviderError("name_taken")
    try:
        ref = store.put(key)
    except SecretStoreUnavailableError:
        raise ProviderError("keyring_unavailable") from None
    provider = ModelProvider(
        workspace_id=workspace.id, name=name, kind=kind, base_url=url, host=host,
        secret_ref=ref, key_hint=key[-4:],
    )  # fmt: skip
    session.add(provider)
    session.flush()
    record_audit(
        session, workspace.id, "model_provider.register", "model_provider", str(provider.id),
        AuditResult.SUCCESS, request_id, {"kind": kind.value, "host": host},
    )  # fmt: skip
    return provider


def remove_provider(
    session: Session, store: SecretStore, provider_id: uuid.UUID, request_id: str | None = None
) -> None:
    """Deletes the key from the keyring and the provider; consents that used it are revoked."""
    provider = session.get(ModelProvider, provider_id)
    if provider is None:
        raise LookupError
    for consent in session.scalars(
        select(AiConsent).where(
            AiConsent.provider_id == provider.id, AiConsent.revoked_at.is_(None)
        )
    ):
        _revoke(session, consent, request_id)
    if provider.secret_ref is not None:
        try:
            store.delete(provider.secret_ref)
        except SecretStoreUnavailableError:
            raise ProviderError("keyring_unavailable") from None
    for row in session.scalars(
        select(ProviderModel).where(ProviderModel.provider_id == provider.id)
    ):
        session.delete(row)
    record_audit(
        session, provider.workspace_id, "model_provider.remove", "model_provider",
        str(provider.id), AuditResult.SUCCESS, request_id, {"host": provider.host},
    )  # fmt: skip
    session.flush()
    session.delete(provider)


def provider_key(store: SecretStore, provider: ModelProvider, host: str) -> str:
    """The provider's key, only to send it to the host it was registered for."""
    if host.lower() != provider.host or provider.secret_ref is None:
        raise ProviderError("key_not_for_this_host")
    try:
        key = store.get(provider.secret_ref)
    except SecretStoreUnavailableError:
        raise ProviderError("keyring_unavailable") from None
    if not key:
        raise ProviderError("key_missing")
    return key


def retention_notice(provider: ModelProvider, model: str, today: date) -> dict[str, Any]:
    return notice_for(provider.kind, provider.host, model.strip(), today)


def _active(session: Session, project: Project) -> list[AiConsent]:
    return list(
        session.scalars(
            select(AiConsent)
            .where(AiConsent.project_id == project.id, AiConsent.revoked_at.is_(None))
            .order_by(AiConsent.created_at)
        )
    )


def describe_consent(consent: AiConsent) -> dict[str, Any]:
    return {
        "id": str(consent.id),
        "provider_id": str(consent.provider_id) if consent.provider_id else None,
        "provider_name": consent.provider_name, "host": consent.host, "model": consent.model,
        "tasks": list(consent.tasks), "retention": dict(consent.retention),
        "consented_by": consent.consented_by, "created_at": consent.created_at.isoformat(),
        "revoked_at": consent.revoked_at.isoformat() if consent.revoked_at else None,
    }  # fmt: skip


def project_ai(session: Session, project: Project, spent: int = 0) -> dict[str, Any]:
    """The project's mode, the consents in force (which task goes to which model), the paths it
    never sends and its monthly token budget with what this month used."""
    return {
        "ai_mode": project.ai_mode.value,
        "consents": [describe_consent(consent) for consent in _active(session, project)],
        "never_send": list(project.never_send or []),
        "remote_token_budget": project.remote_token_budget,
        "remote_tokens_this_month": spent,
        "cache_hits": int(
            session.scalar(
                select(func.coalesce(func.sum(AiCacheEntry.hits), 0)).where(
                    AiCacheEntry.project_id == project.id
                )
            )
            or 0
        ),
        "mask_personal": bool(project.mask_personal),
        "mask_literals": bool(project.mask_literals),
    }


def remote_destinations(session: Session) -> list[str]:
    """Each provider some project may send to now, as "name (host)": for the privacy summary."""
    rows = session.execute(
        select(AiConsent.provider_name, AiConsent.host)
        .where(AiConsent.revoked_at.is_(None))
        .distinct()
        .order_by(AiConsent.provider_name, AiConsent.host)
    )
    return [f"{name} ({host})" for name, host in rows]


MAX_REQUESTS: Final = 200


def recent_requests(session: Session, project: Project, limit: int = 50) -> list[dict[str, Any]]:
    """The local record of what this project sent: when, where, which passages, and tokens.
    Never the content, which stays only in the sources it came from."""
    rows = session.scalars(
        select(AiRequest)
        .where(AiRequest.project_id == project.id)
        .order_by(AiRequest.created_at.desc())
        .limit(max(1, min(limit, MAX_REQUESTS)))
    )
    return [_describe_request(row) for row in rows]


def _describe_request(row: AiRequest) -> dict[str, Any]:
    return {
        "id": str(row.id), "task": row.task, "host": row.host, "model": row.model,
        "answered_by": row.answered_by, "outcome": row.outcome, "sources": list(row.sources),
        "sent_chars": row.sent_chars, "prompt_tokens": row.prompt_tokens,
        "cached_tokens": row.cached_tokens, "completion_tokens": row.completion_tokens,
        "masked": row.masked, "created_at": row.created_at.isoformat(),
    }  # fmt: skip


MAX_BUDGET: Final = 10_000_000_000


def update_settings(
    session: Session,
    project: Project,
    never_send: list[str],
    remote_token_budget: int | None,
    request_id: str | None = None,
    mask_personal: bool = False,
    mask_literals: bool = False,
) -> None:
    """Paths never sent (source exclusion patterns) and the monthly budget (None: no limit)."""
    try:
        patterns = validate_user_patterns(never_send)
    except ValueError:
        raise ProviderError("invalid_patterns") from None
    if remote_token_budget is not None and not 0 <= remote_token_budget <= MAX_BUDGET:
        raise ProviderError("invalid_budget")
    project.never_send = patterns
    project.remote_token_budget = remote_token_budget
    project.mask_personal = mask_personal
    project.mask_literals = mask_literals
    record_audit(
        session, project.workspace_id, "ai_settings.update", "project", str(project.id),
        AuditResult.SUCCESS, request_id,
        {"never_send": len(patterns), "budget": remote_token_budget,
         "mask_personal": mask_personal, "mask_literals": mask_literals},
    )  # fmt: skip


def give_consent(
    session: Session,
    project: Project,
    provider_id: uuid.UUID,
    model: str,
    tasks: list[AiTask],
    retention_acknowledged: bool,
    today: date,
    request_id: str | None = None,
) -> AiConsent:
    """A person consents to send these tasks of this project to this model.

    The retention notice is computed here, not taken from the request: what is recorded is what
    Scrinium showed. A task already sent elsewhere moves to the new consent.
    """
    model = model.strip()
    if not tasks or not 1 <= len(model) <= 200:
        raise ProviderError("invalid_consent")
    if not retention_acknowledged:
        raise ProviderError("retention_not_acknowledged")
    provider = session.get(ModelProvider, provider_id)
    if provider is None or provider.workspace_id != project.workspace_id:
        raise LookupError
    if provider.secret_ref is None:
        raise ProviderError("key_missing")
    # A model consented to is in the catalog, whoever typed it.
    add_model(session, provider.id, model, request_id)
    chosen = sorted({task.value for task in tasks})
    for consent in _active(session, project):
        kept = [task for task in consent.tasks if task not in chosen]
        if kept != list(consent.tasks):
            _revoke(session, consent, request_id)
            if kept:
                _copy(session, consent, kept)
    consent = AiConsent(
        workspace_id=project.workspace_id, project_id=project.id, provider_id=provider.id,
        provider_name=provider.name, host=provider.host, model=model, tasks=chosen,
        retention=retention_notice(provider, model, today),
        consented_by=reviewer_name(session),
    )  # fmt: skip
    session.add(consent)
    project.ai_mode = AiMode.REMOTE_ALLOWED
    session.flush()
    record_audit(
        session, project.workspace_id, "ai_consent.give", "project", str(project.id),
        AuditResult.SUCCESS, request_id,
        {"host": provider.host, "model": model, "tasks": chosen,
         "retention_verified": consent.retention.get("verified", False)},
    )  # fmt: skip
    return consent


def _copy(session: Session, consent: AiConsent, tasks: list[str]) -> None:
    """What is left of a consent when some of its tasks move to another model."""
    session.add(
        AiConsent(
            workspace_id=consent.workspace_id,
            project_id=consent.project_id,
            provider_id=consent.provider_id,
            provider_name=consent.provider_name,
            host=consent.host,
            model=consent.model,
            tasks=tasks,
            retention=dict(consent.retention),
            consented_by=consent.consented_by,
        )
    )
    session.flush()


def _revoke(session: Session, consent: AiConsent, request_id: str | None) -> None:
    consent.revoked_at = utc_now()
    session.flush()
    project = session.get_one(Project, consent.project_id)
    if not _active(session, project):
        project.ai_mode = AiMode.LOCAL_ONLY
    record_audit(
        session, consent.workspace_id, "ai_consent.revoke", "project", str(consent.project_id),
        AuditResult.SUCCESS, request_id, {"host": consent.host, "model": consent.model},
    )  # fmt: skip


def task_to_local(
    session: Session, project: Project, task: AiTask, request_id: str | None = None
) -> bool:
    """This task goes back to the local model; the consent's other tasks stay where they were.
    Returns whether it was going anywhere else."""
    moved = False
    for consent in _active(session, project):
        if task.value not in consent.tasks:
            continue
        moved = True
        kept = [value for value in consent.tasks if value != task.value]
        _revoke(session, consent, request_id)
        if kept:
            _copy(session, consent, kept)
    project.ai_mode = AiMode.REMOTE_ALLOWED if _active(session, project) else AiMode.LOCAL_ONLY
    return moved


def revoke_all(session: Session, project: Project, request_id: str | None = None) -> int:
    """The project goes back to local. What the provider already received cannot be recalled."""
    consents = _active(session, project)
    for consent in consents:
        _revoke(session, consent, request_id)
    project.ai_mode = AiMode.LOCAL_ONLY
    return len(consents)


def consent_for_task(session: Session, project: Project, task: AiTask) -> AiConsent | None:
    """The consent in force for this task, or None: then the task runs locally."""
    if project.ai_mode is not AiMode.REMOTE_ALLOWED:
        return None
    for consent in _active(session, project):
        if task.value in consent.tasks:
            return consent
    return None
