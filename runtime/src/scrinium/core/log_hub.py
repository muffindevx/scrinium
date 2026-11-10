"""Fans logs out to consoles attached with `scrinium attach` (god mode).

While someone is attached, everything is logged at DEBUG; on detach, each logger
returns to its level. Content (prompts, model answers) goes in records marked
`CONTENT` and only reaches whoever asked with `--contenido`: never the normal terminal.
"""

import contextlib
import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Final

from scrinium.core.logging import RedactingFilter

CONTENT: Final = "scrinium_content"
_QUEUE_SIZE: Final = 10_000
# At DEBUG, SQLAlchemy would log every query with its parameters: the source text.
_KEPT_QUIET: Final = ("sqlalchemy",)


def content_extra() -> dict[str, bool]:
    """`extra` for a record with content: logger.debug(..., extra=content_extra())."""
    return {CONTENT: True}


def is_content(record: logging.LogRecord) -> bool:
    return bool(getattr(record, CONTENT, False))


class HideContent(logging.Filter):
    """For the normal terminal: content only goes to whoever attached with `--contenido`."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not is_content(record)


@dataclass(eq=False)
class Subscription:
    content: bool
    # None marks the end: Scrinium is stopping.
    lines: "queue.Queue[str | None]" = field(default_factory=lambda: queue.Queue(_QUEUE_SIZE))
    dropped: int = 0

    def close(self) -> None:
        while True:
            try:
                self.lines.put_nowait(None)
                return
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    self.lines.get_nowait()


class LogHub(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.addFilter(RedactingFilter())
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"
            )
        )
        self._subscribers: list[Subscription] = []
        self._lock = threading.Lock()
        self._levels: dict[str, int] = {}

    def wants_content(self) -> bool:
        return any(subscriber.content for subscriber in self._subscribers)

    def subscribe(self, content: bool) -> Subscription:
        subscription = Subscription(content)
        with self._lock:
            if not self._subscribers:
                self._open_up()
            self._subscribers.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            if subscription in self._subscribers:
                self._subscribers.remove(subscription)
            if not self._subscribers:
                self._close_down()

    def close_all(self) -> None:
        """Ends the attached consoles' streams, so the server closes right away."""
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.close()

    def _open_up(self) -> None:
        root = logging.getLogger()
        self._levels = {"": root.level}
        root.setLevel(logging.DEBUG)
        for name in _KEPT_QUIET:
            logger = logging.getLogger(name)
            self._levels[name] = logger.level
            logger.setLevel(logging.WARNING)
        if self not in root.handlers:
            root.addHandler(self)

    def _close_down(self) -> None:
        root = logging.getLogger()
        if self in root.handlers:
            root.removeHandler(self)
        for name, level in self._levels.items():
            logging.getLogger(name or None).setLevel(level)
        self._levels = {}

    def emit(self, record: logging.LogRecord) -> None:
        content = is_content(record)
        try:
            line = self.format(record)
        except Exception:
            return
        for subscriber in list(self._subscribers):
            if content and not subscriber.content:
                continue
            try:
                subscriber.lines.put_nowait(line)
            except queue.Full:
                subscriber.dropped += 1


HUB: Final = LogHub()
