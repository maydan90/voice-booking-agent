"""An in-memory appointment backend with the two properties a voice agent needs.

1. Every write takes a caller-supplied `request_id` and is idempotent on it. A
   retried create with the same id returns the same appointment; it never
   books twice. A changed payload under the same id is refused.
2. `lookup(request_id)` answers "did that write apply?", which is how the
   agent recovers when an answer was lost (see FlakyService).

The capacity model is deliberately small: `capacity` concurrent appointments
per one-hour slot. Real backends are bigger; the contract is what matters.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .outcomes import Appointment, Booked, Cancelled, Changed, Outcome, Refused, spoken_time
from .policy import DEFAULT_POLICY, Policy


class LostAnswer(Exception):
    """The call may or may not have applied. Never treat as success or failure."""


_REFUSAL_TEXT: dict[str, str] = {
    "outside_hours": "We take appointments on the hour between 9 AM and 4 PM. Nothing was booked.",
    "too_soon": "We need at least two hours' notice for a booking. Nothing was booked.",
    "too_far_ahead": "I can only book up to thirty days ahead. Nothing was booked.",
    "slot_unavailable": "That time is full. Nothing was booked.",
    "party_too_large": "We can seat up to four people per appointment. Nothing was booked.",
    "card_required": "Groups of three or more need a card on file, which I can't take over the phone. Nothing was booked.",
    "not_found": "I couldn't find an appointment with that code. Nothing was changed.",
    "wrong_caller": "That appointment is under a different phone number, so I can't change it from this call. Nothing was changed.",
    "invalid_date": "I didn't catch a valid date and time. Nothing was booked.",
}


def refusal(code: str, alternatives: list[str] | None = None, *, on_change: bool = False) -> Refused:
    """A typed refusal. On a change, the sentence ends with "Nothing was changed." so the model knows the old booking stands."""
    text = _REFUSAL_TEXT[code]
    if on_change:
        text = text.replace("Nothing was booked.", "Nothing was changed.")
    alts = alternatives or []
    if alts:
        spoken = ", ".join(spoken_time(t) for t in alts[:3])
        for ending in ("Nothing was booked.", "Nothing was changed."):
            text = text.replace(ending, f"I do have {spoken}. {ending[:-1]} yet.")
    return Refused(code=code, speakable=text, alternatives=alts)  # type: ignore[arg-type]  # code is a _REFUSAL_TEXT key; a test keeps those keys equal to RefusalCode


@dataclass
class BookingService:
    clock: Callable[[], datetime]
    policy: Policy = DEFAULT_POLICY
    capacity: int = 2
    cards_on_file: set[str] = field(default_factory=set)  # phone numbers with a stored card
    _appointments: dict[str, Appointment] = field(default_factory=dict)
    _by_request: dict[str, tuple[str, str]] = field(default_factory=dict)  # request_id -> (digest, appointment_id)
    _cancelled: set[str] = field(default_factory=set)

    # --- reads ---------------------------------------------------------------

    def availability(self, date: str) -> list[str]:
        """Open HH:MM slots on a date that the policy would accept right now."""
        now = self.clock()
        out = []
        for hour in self.policy.slot_hours():
            when = _parse(date, f"{hour:02d}:00")
            if when is None:
                return []
            if self.policy.check_slot(now, when, 1, has_card=True) is not None:
                continue
            if self._booked_count(date, f"{hour:02d}:00") < self.capacity:
                out.append(f"{hour:02d}:00")
        return out

    def lookup(self, request_id: str) -> Outcome | None:
        """Did a write with this request id apply? Returns its outcome, or None. The recovery primitive."""
        hit = self._by_request.get(request_id)
        if not hit:
            return None
        return self._replay(request_id, hit[0])

    def find(self, appointment_id: str) -> Appointment | None:
        return self._appointments.get(appointment_id.lower())

    # --- writes --------------------------------------------------------------

    def create(self, request_id: str, name: str, phone: str, date: str, time: str, party_size: int) -> Outcome:
        digest = f"create|{name}|{phone}|{date}|{time}|{party_size}"
        replay = self._replay(request_id, digest)
        if replay is not None:
            return replay

        when = _parse(date, time)
        if when is None:
            return refusal("invalid_date")
        code = self.policy.check_slot(self.clock(), when, party_size, has_card=phone in self.cards_on_file)
        if code is not None:
            return refusal(code, self.availability(date) if code in ("slot_unavailable", "outside_hours") else None)
        if self._booked_count(date, time) >= self.capacity:
            return refusal("slot_unavailable", self.availability(date))

        appt = Appointment(
            id=_new_code(),
            name=name,
            phone=phone,
            date=date,
            time=time,
            party_size=party_size,
            request_id=request_id,
        )
        self._appointments[appt.id] = appt
        self._by_request[request_id] = (digest, appt.id)
        return Booked(appointment=appt)

    def change(self, request_id: str, appointment_id: str, phone: str, date: str, time: str) -> Outcome:
        digest = f"change|{appointment_id}|{phone}|{date}|{time}"
        replay = self._replay(request_id, digest)
        if replay is not None:
            return replay

        current = self.find(appointment_id)
        if current is None or current.id in self._cancelled:
            return refusal("not_found", on_change=True)
        if current.phone != phone:
            return refusal("wrong_caller", on_change=True)
        when = _parse(date, time)
        if when is None:
            return refusal("invalid_date", on_change=True)
        code = self.policy.check_slot(self.clock(), when, current.party_size, has_card=True)
        if code is not None:
            return refusal(code, self.availability(date), on_change=True)
        # The caller's own slot does not count against capacity when moving within the same slot.
        taken = self._booked_count(date, time) - (1 if (current.date, current.time) == (date, time) else 0)
        if taken >= self.capacity:
            return refusal("slot_unavailable", self.availability(date), on_change=True)

        updated = Appointment(**{**current.__dict__, "date": date, "time": time, "request_id": request_id})
        self._appointments[updated.id] = updated
        self._by_request[request_id] = (digest, updated.id)
        return Changed(appointment=updated)

    def cancel(self, request_id: str, appointment_id: str, phone: str) -> Outcome:
        digest = f"cancel|{appointment_id}|{phone}"
        replay = self._replay(request_id, digest)
        if replay is not None:
            return replay

        current = self.find(appointment_id)
        if current is None or current.id in self._cancelled:
            return refusal("not_found")
        if current.phone != phone:
            return refusal("wrong_caller")
        when = _parse(current.date, current.time)
        assert when is not None
        fee = self.policy.cancellation_fee_applies(self.clock(), when)
        self._cancelled.add(current.id)
        self._by_request[request_id] = (digest, current.id)
        return Cancelled(appointment=current, fee_applies=fee)

    # --- internals -----------------------------------------------------------

    def _replay(self, request_id: str, digest: str) -> Outcome | None:
        hit = self._by_request.get(request_id)
        if hit is None:
            return None
        prev_digest, appt_id = hit
        if prev_digest != digest:
            raise ValueError(f"request id {request_id} reused with a different payload")
        appt = self._appointments[appt_id]
        kind = prev_digest.split("|", 1)[0]
        if kind == "create":
            return Booked(appointment=appt)
        if kind == "change":
            return Changed(appointment=appt)
        when = _parse(appt.date, appt.time)
        assert when is not None
        return Cancelled(appointment=appt, fee_applies=self.policy.cancellation_fee_applies(self.clock(), when))

    def _booked_count(self, date: str, time: str) -> int:
        return sum(
            1
            for a in self._appointments.values()
            if a.date == date and a.time == time and a.id not in self._cancelled
        )


def _parse(date: str, time: str) -> datetime | None:
    try:
        return datetime.fromisoformat(f"{date}T{time}")
    except ValueError:
        return None


_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/i/l: these get misheard on the phone


def _new_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(5))


__all__ = ["BookingService", "LostAnswer", "refusal"]
