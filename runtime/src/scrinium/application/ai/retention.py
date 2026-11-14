"""What each provider says it keeps, shown when a person chooses a provider and a model.

ADR-010: Scrinium never states retention it has not checked. Each entry was read from the
provider's own pages on `checked_on`, and names them. A provider, host or model without an
entry is shown as "not verified", with no reassurance. Entries older than `STALE_AFTER` are
shown as possibly out of date.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final

from scrinium.domain.enums import ProviderKind

STALE_AFTER: Final = timedelta(days=182)


@dataclass(frozen=True)
class RetentionEntry:
    key: str
    kind: ProviderKind
    # The host the notice applies to: exact, or a domain and its subdomains.
    host_suffix: str
    # Only for models whose name matches; None for every model of that provider.
    model_pattern: re.Pattern[str] | None
    notice: str
    sources: tuple[str, ...]
    checked_on: date


_ANTHROPIC_SOURCES: Final = (
    "https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data",
    "https://platform.claude.com/docs/en/manage-claude/api-and-data-retention",
    "https://www.anthropic.com/legal/commercial-terms",
)

# Most specific first: the first entry that matches is the one shown.
CATALOG: Final[tuple[RetentionEntry, ...]] = (
    RetentionEntry(
        key="anthropic-covered-models",
        kind=ProviderKind.ANTHROPIC,
        host_suffix="api.anthropic.com",
        model_pattern=re.compile(r"^claude-(fable|mythos)-"),
        notice=(
            "Anthropic designa este modelo como «modelo cubierto»: exige guardar las entradas y "
            "salidas 30 días, y no está disponible con retención cero salvo autorización expresa "
            "de Anthropic. El contenido que marquen sus sistemas de seguridad puede guardarse "
            "hasta 2 años, y lo que exija la ley, lo que haga falta. Según sus términos "
            "comerciales, no entrena modelos con el contenido de clientes de la API."
        ),
        sources=_ANTHROPIC_SOURCES,
        checked_on=date(2026, 11, 14),
    ),
    RetentionEntry(
        key="anthropic-api",
        kind=ProviderKind.ANTHROPIC,
        host_suffix="api.anthropic.com",
        model_pattern=None,
        notice=(
            "Anthropic borra las entradas y salidas de la API en su sistema en un plazo de 30 "
            "días (su documentación de la API dice que, salvo en modelos cubiertos, no las "
            "guarda por defecto; Scrinium muestra el plazo más largo). Excepciones: el "
            "contenido que marquen sus sistemas de seguridad puede guardarse hasta 2 años, lo "
            "que exija la ley, y lo que acuerdes con Anthropic. La retención cero se pide a su "
            "equipo de ventas, por organización. Según sus términos comerciales, no entrena "
            "modelos con el contenido de clientes de la API."
        ),
        sources=_ANTHROPIC_SOURCES,
        checked_on=date(2026, 11, 14),
    ),
    RetentionEntry(
        key="zai-api",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        host_suffix="z.ai",
        model_pattern=None,
        notice=(
            "Z.ai dice que el contenido enviado a su API se procesa en tiempo real y no se "
            "guarda en sus servidores (no da un plazo para otros datos de cliente, que guarda "
            "«temporalmente»). Para la API no usa ese contenido para desarrollar o mejorar sus "
            "servicios salvo que lo aceptes expresamente. Procesa los datos, en general, en "
            "Singapur."
        ),
        sources=(
            "https://docs.z.ai/legal-agreement/privacy-policy",
            "https://docs.z.ai/legal-agreement/terms-of-use",
        ),
        checked_on=date(2026, 11, 14),
    ),
)

UNVERIFIED_NOTICE: Final = (
    "Retención no verificada por Scrinium. Lee los términos del proveedor antes de enviar "
    "nada: qué guarda, cuánto tiempo, si entrena con tus datos y dónde los procesa. Asume "
    "que todo lo que envíes queda en manos de ese tercero."
)


def _host_matches(host: str, suffix: str) -> bool:
    host = host.lower().rstrip(".")
    return host == suffix or host.endswith(f".{suffix}")


def find_entry(kind: ProviderKind, host: str, model: str) -> RetentionEntry | None:
    for entry in CATALOG:
        if entry.kind is not kind or not _host_matches(host, entry.host_suffix):
            continue
        if entry.model_pattern is not None and not entry.model_pattern.search(model):
            continue
        return entry
    return None


def notice_for(kind: ProviderKind, host: str, model: str, today: date) -> dict[str, Any]:
    """The notice a person reads and acknowledges before a project sends anything."""
    entry = find_entry(kind, host, model)
    if entry is None:
        return {"verified": False, "key": None, "notice": UNVERIFIED_NOTICE, "sources": [],
                "checked_on": None, "stale": False}  # fmt: skip
    return {
        "verified": True, "key": entry.key, "notice": entry.notice,
        "sources": list(entry.sources), "checked_on": entry.checked_on.isoformat(),
        "stale": today - entry.checked_on > STALE_AFTER,
    }  # fmt: skip
