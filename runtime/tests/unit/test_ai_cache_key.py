"""T5: the key that decides whether a remote answer can be reused (ADR-010)."""

import uuid
from typing import Any

from scrinium.application.ai.routing import _Route, cache_key
from scrinium.domain.enums import AiTask
from scrinium.infrastructure.llm.base import ChatMessage, key_line


def _key(nonce: str, body: str, model: str = "m") -> str:
    adapter: Any = None
    route = _Route(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "api.example.com", model,
                   adapter, (), None)  # fmt: skip
    fenced = f"<<FUENTE 1 {nonce}>>\n{body}\n<<FIN {nonce}>>"
    return cache_key(
        route, AiTask.RULE_MAP, [ChatMessage("user", key_line(nonce) + fenced)], 512, None
    )


def test_the_key_ignores_the_random_fence_key_and_nothing_else() -> None:
    assert _key("a1b2c3d4", "x = 1") == _key("ffee0011", "x = 1")
    assert _key("a1b2c3d4", "x = 1") != _key("a1b2c3d4", "x = 2")
    assert _key("a1b2c3d4", "x = 1") != _key("a1b2c3d4", "x = 1", model="otro")
