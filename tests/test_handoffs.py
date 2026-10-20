from datetime import datetime
from types import SimpleNamespace

from agent.tools import CallState, book_appointment, cancel_appointment
from booking.flaky import FlakyService
from booking.handoffs import HandoffQueue
from booking.service import BookingService

NOW = datetime(2026, 3, 10, 9, 0)


def make(lose_before: set[int] | None = None):
    inner = BookingService(clock=lambda: NOW)
    backend = FlakyService(inner, lose_before=lose_before or set())
    state = CallState(backend=backend, caller_phone="+15550100", handoffs=HandoffQueue(clock=lambda: NOW))
    return SimpleNamespace(userdata=state), state, inner


async def test_unconfirmed_opens_one_handoff_with_what_a_person_needs():
    ctx, state, inner = make(lose_before={0, 1})
    said = await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)

    assert "call you back" in said
    [handoff] = state.handoffs.open_items()
    assert handoff.request_id == state.outcomes[-1].request_id
    assert handoff.caller_phone == "+15550100"
    assert handoff.intent == "create"
    assert handoff.details == {"name": "Dana", "date": "2026-03-12", "time": "14:00", "party_size": 2}
    assert handoff.opened_at == NOW


async def test_model_repeating_the_call_gets_the_same_answer_and_no_new_write_or_ticket():
    ctx, state, inner = make(lose_before={0, 1})
    first = await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    again = await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    third = await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)

    assert first == again == third
    assert state.backend.calls == 2  # the original attempt and one resend; the repeats write nothing
    assert len(state.handoffs.open_items()) == 1
    assert inner._appointments == {}


async def test_a_different_change_after_a_handoff_is_still_attempted():
    ctx, state, inner = make(lose_before={0, 1})
    await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    said = await book_appointment(ctx, name="Dana", date="2026-03-12", time="15:00", party_size=2)

    assert said.startswith("You're booked for 2 on March 12 at 3 PM")
    assert len(state.handoffs.open_items()) == 1  # only the change that stayed unknown


async def test_booked_and_refused_outcomes_open_no_handoff():
    ctx, state, inner = make()
    await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    await book_appointment(ctx, name="Dana", date="2026-03-12", time="23:00", party_size=2)
    await cancel_appointment(ctx, confirmation_code="ZZZZZ")

    assert [o.effect.value for o in state.outcomes] == ["booked", "nothing_changed", "nothing_changed"]
    assert state.handoffs.open_items() == []


def test_queue_is_idempotent_on_the_request_id():
    queue = HandoffQueue(clock=lambda: NOW)
    first, created = queue.open("req_1", "+15550100", "cancel", {"confirmation_code": "K7M2P"})
    second, created_again = queue.open("req_1", "+15550100", "cancel", {"confirmation_code": "K7M2P"})

    assert created and not created_again
    assert first is second
    assert queue.for_request("req_1") is first
    assert queue.for_request("req_2") is None
