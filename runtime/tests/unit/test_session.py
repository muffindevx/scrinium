from scrinium.core.session import (
    LAUNCH_TOKEN_TTL_SECONDS,
    SESSION_TTL_SECONDS,
    LocalSessionManager,
)


class FakeClock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_tokens_are_long_and_unpredictable() -> None:
    manager = LocalSessionManager()
    tokens = {manager.issue_launch_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(token) >= 43 for token in tokens)


def test_launch_token_expires() -> None:
    clock = FakeClock()
    manager = LocalSessionManager(clock)
    token = manager.issue_launch_token()
    clock.now += LAUNCH_TOKEN_TTL_SECONDS
    assert manager.redeem_launch_token(token) is None


def test_new_launch_token_invalidates_the_previous_one() -> None:
    manager = LocalSessionManager()
    old = manager.issue_launch_token()
    new = manager.issue_launch_token()
    assert manager.redeem_launch_token(old) is None
    assert manager.redeem_launch_token(new) is not None


def test_session_expires() -> None:
    clock = FakeClock()
    manager = LocalSessionManager(clock)
    session_id = manager.redeem_launch_token(manager.issue_launch_token())
    assert session_id is not None
    assert manager.validate(session_id) is not None
    clock.now += SESSION_TTL_SECONDS
    assert manager.validate(session_id) is None


def test_session_value_is_not_kept_in_memory() -> None:
    manager = LocalSessionManager()
    session_id = manager.redeem_launch_token(manager.issue_launch_token())
    assert session_id is not None
    session = manager.validate(session_id)
    assert session is not None
    assert session_id not in repr(session)


def test_csrf_comparison() -> None:
    manager = LocalSessionManager()
    session = manager.validate(manager.redeem_launch_token(manager.issue_launch_token()))
    assert session is not None
    assert manager.csrf_matches(session, session.csrf_token)
    assert not manager.csrf_matches(session, "wrong")
    assert not manager.csrf_matches(session, None)
    assert not manager.csrf_matches(session, "ñ")
