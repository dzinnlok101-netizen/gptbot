"""In-process rate limiting helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


class InFlightGuard:
    """Tracks whether a user has a request currently being processed.

    The bot is async and single-process; this is intentionally a tiny
    in-memory set rather than a Redis-backed lock. It only protects against
    a user spamming requests (double-click on a button, two voice messages
    back-to-back, etc.) — not against concurrent processes.
    """

    def __init__(self) -> None:
        self._in_flight: set[int] = set()

    def is_busy(self, user_id: int) -> bool:
        return user_id in self._in_flight

    @contextmanager
    def lock(self, user_id: int) -> Iterator[bool]:
        """Acquire a non-blocking lock for ``user_id``.

        Yields ``True`` if acquired, ``False`` if the user is already busy.
        Releases on context exit.
        """
        if user_id in self._in_flight:
            yield False
            return
        self._in_flight.add(user_id)
        try:
            yield True
        finally:
            self._in_flight.discard(user_id)
