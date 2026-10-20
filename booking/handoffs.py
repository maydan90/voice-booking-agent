"""Handoffs: what stands behind "a team member will call you back".

`Unconfirmed` is the one outcome that promises a person. That sentence is only
true if something a person will see gets created, exactly once, with enough to
act on without replaying the call:

  request_id    to look the write up and see whether it applied
  caller_phone  to call back
  intent        what the caller was doing (create, change, cancel)
  details       the arguments of that change (date, time, party size, code)

The queue is keyed on the request id. A model that repeats the same tool call
after hearing "I won't keep trying" gets the same handoff back, not a second
ticket, and the tool does not write again for that change: a person owns it
now, and an agent write racing their callback is how a booking gets made twice.

In memory here. A real deployment points this at the ticketing or CRM system,
with the same idempotency on the request id.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime
from typing import Callable


@dataclass(frozen=True)
class Handoff:
    id: str
    request_id: str
    caller_phone: str
    intent: str
    details: dict[str, object]
    opened_at: datetime


class HandoffQueue:
    def __init__(self, clock: Callable[[], datetime] = datetime.now):
        self._clock = clock
        self._by_request: dict[str, Handoff] = {}
        self._seq = itertools.count(1)

    def open(self, request_id: str, caller_phone: str, intent: str, details: dict[str, object]) -> tuple[Handoff, bool]:
        """Opens a handoff for this change, or returns the one already open.

        Returns (handoff, created). Idempotent on request_id: the second call
        for the same change returns the first handoff with created=False.
        """
        existing = self._by_request.get(request_id)
        if existing is not None:
            return existing, False
        handoff = Handoff(
            id=f"ho_{next(self._seq)}",
            request_id=request_id,
            caller_phone=caller_phone,
            intent=intent,
            details=dict(details),
            opened_at=self._clock(),
        )
        self._by_request[request_id] = handoff
        return handoff, True

    def for_request(self, request_id: str) -> Handoff | None:
        return self._by_request.get(request_id)

    def open_items(self) -> list[Handoff]:
        return list(self._by_request.values())
