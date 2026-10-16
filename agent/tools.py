"""The tools the agent can call. Each returns a sentence to say, never an error.

Three rules are enforced here, outside the model:

1. Outcomes are typed and speakable (booking/outcomes.py). The model relays
   `speakable` verbatim. "Nothing was booked" ends retry loops; "I couldn't
   confirm" ends the conversation's write attempts and hands off.
2. Every write carries a per-change request id (agent/request_ids.py). A
   retry, by the model or by this code, reuses it, so the backend dedupes.
3. A lost answer is recovered, not retried blindly: look up by request id
   first; resend once with the same id only if the write did not apply;
   otherwise return Unconfirmed. Bounded, and never a double booking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from livekit.agents import RunContext, function_tool

from booking.outcomes import Appointment, Outcome, Refused, Unconfirmed, spoken_date, spoken_time
from booking.service import LostAnswer

from .request_ids import RequestIds

log = logging.getLogger("booking-agent")


class BookingBackend(Protocol):
    def availability(self, date: str) -> list[str]: ...
    def lookup(self, request_id: str) -> Outcome | None: ...
    def find(self, appointment_id: str) -> Appointment | None: ...
    def create(self, request_id: str, name: str, phone: str, date: str, time: str, party_size: int) -> Outcome: ...
    def change(self, request_id: str, appointment_id: str, phone: str, date: str, time: str) -> Outcome: ...
    def cancel(self, request_id: str, appointment_id: str, phone: str) -> Outcome: ...


@dataclass
class CallState:
    """Per-call state carried in the AgentSession's userdata."""

    backend: BookingBackend
    caller_phone: str
    request_ids: RequestIds = field(default_factory=RequestIds)
    outcomes: list[Outcome] = field(default_factory=list)  # every outcome spoken, for evals and logs


def _record(state: CallState, outcome: Outcome) -> str:
    state.outcomes.append(outcome)
    log.info("outcome code=%s effect=%s", outcome.code, outcome.effect.value)
    return outcome.speakable


def _recover(state: CallState, request_id: str, resend) -> Outcome:
    """After a lost answer: look before writing again, resend once, then give up honestly."""
    found = state.backend.lookup(request_id)
    if found is not None:
        return found  # the write applied; this is its outcome, no second write
    try:
        return resend()  # same request id, so even a late-arriving first attempt cannot double-apply
    except LostAnswer:
        # Second lost answer. Do not loop; the backend's request id guarantees that
        # whichever attempt applied, at most one appointment exists.
        return Unconfirmed(request_id=request_id)


@function_tool
async def check_availability(context: RunContext[CallState], date: str) -> str:
    """Lists open appointment times on a date.

    Args:
        date: The date in YYYY-MM-DD format.
    """
    state = context.userdata
    slots = state.backend.availability(date)
    if not slots:
        return "There are no open times on that date."
    spoken = ", ".join(spoken_time(s) for s in slots)
    return f"On {spoken_date(date)} we have {spoken}."


@function_tool
async def book_appointment(context: RunContext[CallState], name: str, date: str, time: str, party_size: int) -> str:
    """Books an appointment for the caller. Call this once you have the name, date, time and party size.

    Args:
        name: The caller's name for the booking.
        date: The date in YYYY-MM-DD format.
        time: The start time in 24-hour HH:MM format, on the hour.
        party_size: Number of people, 1 to 4.
    """
    state = context.userdata
    phone = state.caller_phone
    request_id = state.request_ids.for_change("create", name=name, phone=phone, date=date, time=time, party_size=party_size)

    def attempt() -> Outcome:
        return state.backend.create(request_id, name, phone, date, time, party_size)

    try:
        outcome = attempt()
    except LostAnswer:
        outcome = _recover(state, request_id, attempt)
    return _record(state, outcome)


@function_tool
async def change_appointment(context: RunContext[CallState], confirmation_code: str, date: str, time: str) -> str:
    """Moves an existing appointment to a new date and time.

    Args:
        confirmation_code: The five-character confirmation code the caller was given.
        date: The new date in YYYY-MM-DD format.
        time: The new start time in 24-hour HH:MM format, on the hour.
    """
    state = context.userdata
    phone = state.caller_phone
    request_id = state.request_ids.for_change("change", code=confirmation_code.lower(), phone=phone, date=date, time=time)

    def attempt() -> Outcome:
        return state.backend.change(request_id, confirmation_code, phone, date, time)

    try:
        outcome = attempt()
    except LostAnswer:
        outcome = _recover(state, request_id, attempt)
    return _record(state, outcome)


@function_tool
async def cancel_appointment(context: RunContext[CallState], confirmation_code: str) -> str:
    """Cancels an existing appointment. Only call this after the caller has clearly confirmed they want to cancel.

    Args:
        confirmation_code: The five-character confirmation code the caller was given.
    """
    state = context.userdata
    phone = state.caller_phone
    request_id = state.request_ids.for_change("cancel", code=confirmation_code.lower(), phone=phone)

    def attempt() -> Outcome:
        return state.backend.cancel(request_id, confirmation_code, phone)

    try:
        outcome = attempt()
    except LostAnswer:
        outcome = _recover(state, request_id, attempt)
    return _record(state, outcome)


ALL_TOOLS = [check_availability, book_appointment, change_appointment, cancel_appointment]

__all__ = ["CallState", "ALL_TOOLS", "check_availability", "book_appointment", "change_appointment", "cancel_appointment", "Refused"]
