"""AI policy: where a project's text may go (ADR-004, ADR-010).

Every project is `local_only` unless a person consented to a remote model for it. A
`local_only` project can only use providers that run on this machine; this check stops a
remote one from slipping in without that consent.
"""

from scrinium.domain.enums import AiMode
from scrinium.infrastructure.llm.base import LLMProvider
from scrinium.infrastructure.sqlite.models import Project

__all__ = ["AiMode", "RemoteProviderNotAllowedError", "project_mode", "require_local"]


class RemoteProviderNotAllowedError(RuntimeError):
    pass


def require_local(provider: LLMProvider, mode: AiMode = AiMode.LOCAL_ONLY) -> LLMProvider:
    """The provider, if the mode allows it. A remote provider needs `REMOTE_ALLOWED`."""
    if mode is AiMode.LOCAL_ONLY and not provider.is_local:
        raise RemoteProviderNotAllowedError
    return provider


def project_mode(project: Project) -> AiMode:
    """The project's mode; anything unexpected counts as local, never as permission."""
    return AiMode.REMOTE_ALLOWED if project.ai_mode == AiMode.REMOTE_ALLOWED else AiMode.LOCAL_ONLY
