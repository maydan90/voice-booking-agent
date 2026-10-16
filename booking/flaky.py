"""A wrapper that loses answers on purpose.

`FlakyService` forwards every call to a real `BookingService`, but for calls
whose index appears in `lose_after` it performs the write and then raises
`LostAnswer`; for `lose_before` it raises before performing anything. This is
the deterministic stand-in for timeouts and dropped connections, and it is
what the tool layer's recovery path is tested against.
"""

from __future__ import annotations

from typing import Any

from .outcomes import Appointment, Outcome
from .service import BookingService, LostAnswer


class FlakyService:
    def __init__(self, inner: BookingService, lose_before: set[int] | None = None, lose_after: set[int] | None = None):
        self.inner = inner
        self.lose_before = lose_before or set()
        self.lose_after = lose_after or set()
        self.calls = 0
        self.log: list[str] = []

    # reads are passed through untouched
    def availability(self, date: str) -> list[str]:
        return self.inner.availability(date)

    def lookup(self, request_id: str) -> Outcome | None:
        self.log.append(f"lookup {request_id}")
        return self.inner.lookup(request_id)

    def find(self, appointment_id: str) -> Appointment | None:
        return self.inner.find(appointment_id)

    @property
    def cards_on_file(self) -> set[str]:
        return self.inner.cards_on_file

    # writes may lose their answer
    def create(self, *a: Any, **kw: Any) -> Outcome:
        return self._write("create", self.inner.create, *a, **kw)

    def change(self, *a: Any, **kw: Any) -> Outcome:
        return self._write("change", self.inner.change, *a, **kw)

    def cancel(self, *a: Any, **kw: Any) -> Outcome:
        return self._write("cancel", self.inner.cancel, *a, **kw)

    def _write(self, name: str, fn: Any, *a: Any, **kw: Any) -> Outcome:
        idx = self.calls
        self.calls += 1
        if idx in self.lose_before:
            self.log.append(f"{name} #{idx} lost before apply")
            raise LostAnswer("connection reset before the request was sent")
        out = fn(*a, **kw)
        if idx in self.lose_after:
            self.log.append(f"{name} #{idx} applied, answer lost")
            raise LostAnswer("read timeout after the request was sent")
        self.log.append(f"{name} #{idx} ok")
        return out
