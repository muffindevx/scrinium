"""AI policy: today only the `local_only` mode exists (ADR-004).

There is no remote provider in the code. If there ever is one, it will need a new mode with
explicit consent; this check stops one from slipping in without that step.
"""

from enum import StrEnum

from scrinium.infrastructure.llm.base import LLMProvider


class AiMode(StrEnum):
    LOCAL_ONLY = "local_only"


class RemoteProviderNotAllowedError(RuntimeError):
    pass


def require_local(provider: LLMProvider, mode: AiMode = AiMode.LOCAL_ONLY) -> LLMProvider:
    if mode is AiMode.LOCAL_ONLY and not provider.is_local:
        raise RemoteProviderNotAllowedError
    return provider
