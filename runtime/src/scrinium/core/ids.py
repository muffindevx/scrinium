"""Time-ordered UUIDs (RFC 9562, version 7) for new knowledge entities (ADR-011).

Python 3.12 has no `uuid.uuid7`. A v7 id starts with the creation time in milliseconds, so ids
sort by creation in files, exports and indexes; the rest is random.
"""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    millis = time.time_ns() // 1_000_000
    random = int.from_bytes(os.urandom(10))
    value = (millis & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76  # version
    value |= (random >> 68) << 64  # 12 random bits
    value |= 0b10 << 62  # variant
    value |= random & ((1 << 62) - 1)
    return uuid.UUID(int=value)
