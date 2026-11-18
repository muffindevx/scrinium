"""The evidence graph rule ↔ commit ↔ ticket (phase 10.4, ADR-014, annex C.4).

A rule reaches its tickets through the commits that last changed its cited lines (`git blame`,
stored by `knowledge.tickets.label_rules`), and through a person:

| Level      | How                                                         |
|------------|-------------------------------------------------------------|
| confirmed  | a person linked the ticket to the rule                      |
| explicit   | the tracker attaches one of those commits to the ticket     |
| mentioned  | one of those commits names the ticket's key, current or old |

Each ticket appears once, at its strongest level, with the commits that support it. Keys no
ticket matches are still returned, as today: a label from a commit message, nothing more.
Nothing here is guessed; inferred links (same period, same files) are proposals for the review
inbox and are not shown as links.
"""

import uuid
from typing import Any, Final

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from scrinium.application.workspaces import record_audit, reviewer_name
from scrinium.domain.enums import AuditResult, TicketLinkLevel
from scrinium.infrastructure.sqlite.models import KnowledgeEntity, Ticket, TicketLink

_STRENGTH: Final = {
    TicketLinkLevel.CONFIRMED: 3,
    TicketLinkLevel.EXPLICIT: 2,
    TicketLinkLevel.MENTIONED: 1,
    TicketLinkLevel.INFERRED: 0,
}
RULE: Final = "rule"
COMMIT: Final = "commit"


def describe_ticket(ticket: Ticket) -> dict[str, Any]:
    """Metadata only (ADR-014, decision 6): never a description, a comment or a person."""
    return {
        "id": str(ticket.id),
        "key": ticket.key,
        "previous_keys": list(ticket.previous_keys or []),
        "title": ticket.title,
        "type": ticket.ticket_type,
        "status": ticket.status,
        "status_category": ticket.status_category,
        "labels": list(ticket.labels or []),
        "url": ticket.url,
        "opened_at": None if ticket.opened_at is None else ticket.opened_at.isoformat(),
        "changed_at": None if ticket.changed_at is None else ticket.changed_at.isoformat(),
        "closed_at": None if ticket.closed_at is None else ticket.closed_at.isoformat(),
        "deleted": ticket.tombstone,
    }


def tickets_by_key(session: Session, project_id: uuid.UUID, keys: list[str]) -> dict[str, Ticket]:
    """Each key's ticket in this project, by its current key or a previous one. A current key
    wins over an old one that another ticket used to have."""
    if not keys:
        return {}
    found: dict[str, Ticket] = {}
    old = text(
        "EXISTS (SELECT 1 FROM json_each(tickets.previous_keys) WHERE json_each.value IN "
        "(SELECT value FROM json_each(:keys)))"
    ).bindparams(keys=_json_list(keys))
    rows = session.scalars(
        select(Ticket).where(Ticket.project_id == project_id, or_(Ticket.key.in_(keys), old))
    ).all()
    for ticket in sorted(rows, key=lambda row: row.tombstone):
        found.setdefault(ticket.key, ticket)
    for ticket in sorted(rows, key=lambda row: row.tombstone):
        for key in ticket.previous_keys or []:
            if key in keys:
                found.setdefault(key, ticket)
    return {key: found[key] for key in keys if key in found}


def rule_tickets(session: Session, entity: KnowledgeEntity) -> dict[str, Any]:
    """The rule's tickets, strongest level first, and the keys no ticket matched."""
    metadata = entity.metadata_ or {}
    keys = [key for key in metadata.get("tickets") or [] if isinstance(key, str)]
    commits_by_key = metadata.get("ticket_commits") or {}
    commits = [sha for sha in metadata.get("commits") or [] if isinstance(sha, str)]
    found: dict[uuid.UUID, dict[str, Any]] = {}

    def add(ticket: Ticket, level: TicketLinkLevel, via: list[str]) -> None:
        current = found.get(ticket.id)
        if current is None:
            found[ticket.id] = {
                **describe_ticket(ticket), "level": level.value, "commits": list(via),
            }  # fmt: skip
            return
        if _STRENGTH[level] > _STRENGTH[TicketLinkLevel(current["level"])]:
            current["level"] = level.value
        current["commits"] = list(dict.fromkeys([*current["commits"], *via]))

    for ticket in session.scalars(
        select(Ticket)
        .join(TicketLink, Ticket.id == TicketLink.ticket_id)
        .where(
            TicketLink.project_id == entity.project_id,
            TicketLink.from_kind == RULE,
            TicketLink.from_ref == str(entity.id),
            TicketLink.level == TicketLinkLevel.CONFIRMED,
        )
    ):
        add(ticket, TicketLinkLevel.CONFIRMED, [])
    if commits:
        for link, ticket in session.execute(
            select(TicketLink, Ticket)
            .join(Ticket, Ticket.id == TicketLink.ticket_id)
            .where(
                TicketLink.project_id == entity.project_id,
                TicketLink.from_kind == COMMIT,
                TicketLink.from_ref.in_(commits),
            )
        ):
            add(ticket, TicketLinkLevel.EXPLICIT, [link.from_ref])
    matched = tickets_by_key(session, entity.project_id, keys)
    for key, ticket in matched.items():
        via = [sha for sha in commits_by_key.get(key) or [] if isinstance(sha, str)]
        add(ticket, TicketLinkLevel.MENTIONED, via)
    ordered = sorted(
        found.values(),
        key=lambda item: (-_STRENGTH[TicketLinkLevel(item["level"])], item["deleted"], item["key"]),
    )
    return {"tickets": ordered, "unmatched_keys": [key for key in keys if key not in matched]}


def confirm_link(
    session: Session,
    entity: KnowledgeEntity,
    ticket_id: uuid.UUID,
    request_id: str | None = None,
) -> TicketLink:
    """A person says this ticket explains this rule."""
    ticket = session.get(Ticket, ticket_id)
    if ticket is None or ticket.project_id != entity.project_id:
        raise LookupError
    existing = session.scalars(
        select(TicketLink).where(
            TicketLink.ticket_id == ticket.id,
            TicketLink.from_kind == RULE,
            TicketLink.from_ref == str(entity.id),
            TicketLink.level == TicketLinkLevel.CONFIRMED,
        )
    ).first()
    if existing is not None:
        return existing
    link = TicketLink(
        workspace_id=entity.workspace_id,
        project_id=entity.project_id,
        ticket_id=ticket.id,
        from_kind=RULE,
        from_ref=str(entity.id),
        level=TicketLinkLevel.CONFIRMED,
        origin=f"person:{reviewer_name(session) or ''}"[:100],
    )
    session.add(link)
    session.flush()
    record_audit(
        session, entity.workspace_id, "ticket_link.confirm", "knowledge_entity", str(entity.id),
        AuditResult.SUCCESS, request_id, {"ticket": ticket.key},
    )  # fmt: skip
    return link


def remove_link(
    session: Session, entity: KnowledgeEntity, ticket_id: uuid.UUID, request_id: str | None = None
) -> bool:
    """Removes a person's link. Links that come from commits or the tracker cannot be removed
    here: they are facts about the history, not opinions."""
    link = session.scalars(
        select(TicketLink).where(
            TicketLink.ticket_id == ticket_id,
            TicketLink.from_kind == RULE,
            TicketLink.from_ref == str(entity.id),
            TicketLink.level == TicketLinkLevel.CONFIRMED,
        )
    ).first()
    if link is None:
        return False
    session.delete(link)
    record_audit(
        session, entity.workspace_id, "ticket_link.remove", "knowledge_entity", str(entity.id),
        AuditResult.SUCCESS, request_id, {"ticket_id": str(ticket_id)},
    )  # fmt: skip
    return True


def _json_list(values: list[str]) -> str:
    import json

    return json.dumps(values)
