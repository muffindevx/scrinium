"""\"Probar conexión\" for a remote provider (ADR-010): one fixed sentence, nothing from a project.

It says which part failed, so the person knows what to fix: the key, the model name, or a
request the provider does not accept (with the provider's own error code and message, from
which keys are removed). Its request is fixed, so that message cannot quote project content.
"""

from typing import Any, Final

from scrinium.infrastructure.llm.base import ChatMessage, LLMProvider, OutputBudgetExceededError
from scrinium.infrastructure.local_http import ModelError, ProviderUnavailableError
from scrinium.infrastructure.remote_http import ProviderAuthError, ProviderRequestError

STATUS_PROMPT: Final = [ChatMessage("user", "Responde solo con la palabra: listo")]
# Room for the word even if the model writes a little before it.
STATUS_MAX_TOKENS: Final = 64
# Error codes providers use when the model does not exist or this key cannot use it.
_MODEL_KINDS: Final = frozenset({"not_found_error", "model_not_found", "invalid_model"})


def check_connection(adapter: LLMProvider) -> dict[str, Any]:
    try:
        completion = adapter.complete(STATUS_PROMPT, STATUS_MAX_TOKENS)
    except ProviderAuthError:
        return {"status": "model_missing", "reason": "key_rejected"}
    except ProviderRequestError as error:
        not_found = error.status == 404 or error.kind in _MODEL_KINDS
        return {
            "status": "model_missing",
            "reason": "model_not_found" if not_found else "request_rejected",
            "http_status": error.status, "error_type": error.kind, "detail": error.detail,
        }  # fmt: skip
    except ProviderUnavailableError:
        return {"status": "unavailable", "reason": "unavailable"}
    except OutputBudgetExceededError:
        return {"status": "unavailable", "reason": "no_room"}
    except ModelError:
        return {"status": "unavailable", "reason": "no_answer"}
    return {"status": "ready", "reason": "ok", "answered_by": completion.model_id}
