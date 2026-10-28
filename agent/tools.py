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
4. Unconfirmed opens a handoff (booking/handoffs.py), once per change. If the
   model calls the tool again for a change already handed off, nothing is
   written: a person owns it now, and the caller hears the same sentence.

Every tool call also appends one line to the call's transcript
(agent/transcript.py): tool, request id, outcome code and effect, latency and
the sentence returned to the model. That file is what an eval on a real call
reads later.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from time import perf_counter
from typing import Protocol

from livekit.agents import RunContext, function_tool

from booking.handoffs import HandoffQueue
from booking.outcomes import Appointment, Outcome, Refused, Unconfirmed, spoken_date, spoken_time
from booking.service import LostAnswer

from .request_ids import RequestIds
from .transcript import TranscriptLog

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
    call_id: str = field(default_factory=lambda: secrets.token_hex(6))  # the room name on a real call
    request_ids: RequestIds = field(default_factory=RequestIds)
    outcomes: list[Outcome] = field(default_factory=list)  # every outcome spoken, for evals and logs
    # One queue per call by default; a real deployment injects the shared queue
    # that its support team works from.
    handoffs: HandoffQueue = field(default_factory=HandoffQueue)
    # In memory by default; agent/main.py gives it a file when TRANSCRIPTS_DIR is set.
    transcript: TranscriptLog = field(default_factory=TranscriptLog)


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _record(state: CallState, tool: str, started: float, request_id: str, outcome: Outcome) -> str:
    state.outcomes.append(outcome)
    log.info("outcome code=%s effect=%s", outcome.code, outcome.effect.value)
    handoff = state.handoffs.for_request(request_id)
    state.transcript.record(
        call_id=state.call_id,
        tool=tool,
        request_id=request_id,
        code=outcome.code,
        effect=outcome.effect.value,
        latency_ms=_elapsed_ms(started),
        handoff_id=handoff.id if handoff is not None else None,
        said=outcome.speakable,
    )
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


def _write(state: CallState, intent: str, request_id: str, details: dict[str, object], attempt) -> Outcome:
    """One write with recovery, and a handoff when the outcome stays unknown."""
    if state.handoffs.for_request(request_id) is not None:
        # Already with a person. Writing again here could race their callback.
        log.info("handoff already open for %s; not writing again", request_id)
        return Unconfirmed(request_id=request_id)
    try:
        outcome = attempt()
    except LostAnswer:
        outcome = _recover(state, request_id, attempt)
    if isinstance(outcome, Unconfirmed):
        handoff, created = state.handoffs.open(request_id, state.caller_phone, intent, details)
        if created:
            log.warning("handoff opened id=%s request_id=%s intent=%s", handoff.id, request_id, intent)
    return outcome


@function_tool
async def check_availability(context: RunContext[CallState], date: str) -> str:
    """Lists open appointment times on a date.

    Args:
        date: The date in YYYY-MM-DD format.
    """
    state = context.userdata
    started = perf_counter()
    slots = state.backend.availability(date)
    if slots:
        said = f"On {spoken_date(date)} we have {', '.join(spoken_time(s) for s in slots)}."
    else:
        said = "There are no open times on that date."
    state.transcript.record(
        call_id=state.call_id,
        tool="check_availability",
        request_id=None,
        code=None,
        effect=None,
        latency_ms=_elapsed_ms(started),
        handoff_id=None,
        said=said,
    )
    return said


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
    started = perf_counter()
    phone = state.caller_phone
    request_id = state.request_ids.for_change("create", name=name, phone=phone, date=date, time=time, party_size=party_size)

    def attempt() -> Outcome:
        return state.backend.create(request_id, name, phone, date, time, party_size)

    details = {"name": name, "date": date, "time": time, "party_size": party_size}
    return _record(state, "book_appointment", started, request_id, _write(state, "create", request_id, details, attempt))


@function_tool
async def change_appointment(context: RunContext[CallState], confirmation_code: str, date: str, time: str) -> str:
    """Moves an existing appointment to a new date and time.

    Args:
        confirmation_code: The five-character confirmation code the caller was given.
        date: The new date in YYYY-MM-DD format.
        time: The new start time in 24-hour HH:MM format, on the hour.
    """
    state = context.userdata
    started = perf_counter()
    phone = state.caller_phone
    request_id = state.request_ids.for_change("change", code=confirmation_code.lower(), phone=phone, date=date, time=time)

    def attempt() -> Outcome:
        return state.backend.change(request_id, confirmation_code, phone, date, time)

    details = {"confirmation_code": confirmation_code, "date": date, "time": time}
    return _record(state, "change_appointment", started, request_id, _write(state, "change", request_id, details, attempt))


@function_tool
async def cancel_appointment(context: RunContext[CallState], confirmation_code: str) -> str:
    """Cancels an existing appointment. Only call this after the caller has clearly confirmed they want to cancel.

    Args:
        confirmation_code: The five-character confirmation code the caller was given.
    """
    state = context.userdata
    started = perf_counter()
    phone = state.caller_phone
    request_id = state.request_ids.for_change("cancel", code=confirmation_code.lower(), phone=phone)

    def attempt() -> Outcome:
        return state.backend.cancel(request_id, confirmation_code, phone)

    details = {"confirmation_code": confirmation_code}
    return _record(state, "cancel_appointment", started, request_id, _write(state, "cancel", request_id, details, attempt))


ALL_TOOLS = [check_availability, book_appointment, change_appointment, cancel_appointment]

__all__ = ["CallState", "ALL_TOOLS", "check_availability", "book_appointment", "change_appointment", "cancel_appointment", "Refused"]
