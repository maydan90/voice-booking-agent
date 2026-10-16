"""Decisions that must never reach the model.

Business rules live here, in code, and are enforced by the tools before the
backend is called. The system prompt tells the agent these rules exist so it
can set expectations, but the agent cannot be talked out of them: a caller
who says "just this once, make it 11 pm" gets a refusal from the tool, and
the tool's speakable text is what the agent says.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Policy:
    open_hour: int = 9  # first appointment 09:00
    close_hour: int = 17  # last appointment starts 16:00 (slots are one hour)
    min_lead: timedelta = timedelta(hours=2)
    max_horizon: timedelta = timedelta(days=30)
    max_party: int = 4
    card_required_from_party: int = 3  # groups of 3+ must hold a card on file
    free_cancel_window: timedelta = timedelta(hours=24)

    def slot_hours(self) -> list[int]:
        return list(range(self.open_hour, self.close_hour))

    def check_slot(self, now: datetime, when: datetime, party_size: int, has_card: bool) -> str | None:
        """Returns a refusal code, or None when the request is allowed."""
        if party_size > self.max_party:
            return "party_too_large"
        if when.hour not in self.slot_hours() or when.minute != 0:
            return "outside_hours"
        if when - now < self.min_lead:
            return "too_soon"
        if when - now > self.max_horizon:
            return "too_far_ahead"
        if party_size >= self.card_required_from_party and not has_card:
            return "card_required"
        return None

    def cancellation_fee_applies(self, now: datetime, when: datetime) -> bool:
        return when - now < self.free_cancel_window


DEFAULT_POLICY = Policy()
