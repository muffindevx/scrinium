import time

from scrinium.core.ids import uuid7


def test_uuid7_is_version_7_unique_and_sorts_by_millisecond() -> None:
    ids = [uuid7() for _ in range(200)]
    assert {i.version for i in ids} == {7}
    assert len(set(ids)) == 200
    # Ordered by creation across milliseconds; within one, the order is random (RFC 9562).
    earlier = uuid7()
    time.sleep(0.002)
    assert earlier < uuid7()
