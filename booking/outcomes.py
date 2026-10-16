"""Typed outcomes a voice agent can say out loud.

Every backend call the agent makes returns one of these, never a raw error.
Each outcome carries three things the agent needs:

  code       a stable identifier the eval suite and logs key on
  effect     what is true in the world now: BOOKED, NOTHING_CHANGED or UNCONFIRMED
  speakable  the sentence the agent says verbatim to the caller

The `effect` field is the important one. An LLM that receives a raw 400 does
not know whether to retry or apologise, and in practice it retries, three
times in four seconds. An outcome that says "nothing was booked" ends that
loop, and one that says "I could not confirm it" tells the model to stop and
hand off rather than try again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class Effect(str, Enum):
    BOOKED = "booked"
    CHANGED = "changed"
    CANCELLED = "cancelled"
    NOTHING_CHANGED = "nothing_changed"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True)
class Appointment:
    id: str
    name: str
    phone: str
    date: str  # YYYY-MM-DD
    time: str  # HH:MM, 24h
    party_size: int
    request_id: str


@dataclass(frozen=True)
class Booked:
    appointment: Appointment
    code: Literal["booked"] = "booked"
    effect: Effect = Effect.BOOKED

    @property
    def speakable(self) -> str:
        a = self.appointment
        return (
            f"You're booked for {a.party_size} on {spoken_date(a.date)} at {spoken_time(a.time)}. "
            f"Your confirmation code is {spoken_code(a.id)}."
        )


@dataclass(frozen=True)
class Changed:
    appointment: Appointment
    code: Literal["changed"] = "changed"
    effect: Effect = Effect.CHANGED

    @property
    def speakable(self) -> str:
        a = self.appointment
        return f"Done. Your appointment is now on {spoken_date(a.date)} at {spoken_time(a.time)}."


@dataclass(frozen=True)
class Cancelled:
    appointment: Appointment
    fee_applies: bool
    code: Literal["cancelled"] = "cancelled"
    effect: Effect = Effect.CANCELLED

    @property
    def speakable(self) -> str:
        base = f"Your appointment on {spoken_date(self.appointment.date)} is cancelled."
        if self.fee_applies:
            return base + " Because it was within 24 hours, the late cancellation fee applies."
        return base + " There is no charge."


RefusalCode = Literal[
    "outside_hours",
    "too_soon",
    "too_far_ahead",
    "slot_unavailable",
    "party_too_large",
    "card_required",
    "not_found",
    "wrong_caller",
    "invalid_date",
]


@dataclass(frozen=True)
class Refused:
    """The backend definitively said no. Nothing changed, and the model must not retry."""

    code: RefusalCode
    speakable: str
    alternatives: list[str] = field(default_factory=list)  # HH:MM slots on the same day, if any
    effect: Effect = Effect.NOTHING_CHANGED


@dataclass(frozen=True)
class Unconfirmed:
    """We asked, got no answer, looked it up, and still do not know. Hand off."""

    request_id: str
    code: Literal["unconfirmed"] = "unconfirmed"
    effect: Effect = Effect.UNCONFIRMED

    @property
    def speakable(self) -> str:
        return (
            "I wasn't able to confirm that with our system just now. I haven't booked anything twice, "
            "and I won't keep trying on my own. A team member will call you back shortly to finish this."
        )


Outcome = Booked | Changed | Cancelled | Refused | Unconfirmed


# --- speech helpers -------------------------------------------------------------

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def spoken_date(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{_MONTHS[m - 1]} {d}"


def spoken_time(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12} {suffix}" if m == 0 else f"{h12}:{m:02d} {suffix}"


def spoken_code(code: str) -> str:
    """Confirmation codes are read letter by letter so TTS does not invent a word."""
    return " ".join(code.upper())
