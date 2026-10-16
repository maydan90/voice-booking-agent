from datetime import datetime
from types import SimpleNamespace

from agent.request_ids import RequestIds
from agent.tools import CallState, book_appointment, cancel_appointment, change_appointment, check_availability
from booking.flaky import FlakyService
from booking.service import BookingService

NOW = datetime(2026, 3, 10, 9, 0)


def make(flaky: dict | None = None):
    inner = BookingService(clock=lambda: NOW)
    backend = FlakyService(inner, **flaky) if flaky else inner
    state = CallState(backend=backend, caller_phone="+15550100")
    return SimpleNamespace(userdata=state), state, inner


async def test_tools_speak_typed_outcomes():
    ctx, state, _ = make()
    assert "9 AM" in await check_availability(ctx, date="2026-03-12")
    said = await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    assert said.startswith("You're booked for 2 on March 12 at 2 PM")
    assert state.outcomes[-1].effect.value == "booked"


async def test_model_retry_with_same_arguments_reuses_the_request_id():
    ctx, state, inner = make()
    first = await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    second = await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    assert first == second
    assert len(inner._appointments) == 1
    assert state.outcomes[0].appointment.request_id == state.outcomes[1].appointment.request_id


async def test_a_different_change_gets_a_different_request_id():
    ids = RequestIds()
    a = ids.for_change("create", date="2026-03-12", time="14:00")
    b = ids.for_change("create", date="2026-03-12", time="15:00")
    assert a != b and a == ids.for_change("create", time="14:00", date="2026-03-12")


async def test_lost_answer_after_apply_is_recovered_without_a_second_write():
    ctx, state, inner = make({"lose_after": {0}})
    said = await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    assert said.startswith("You're booked")
    assert len(inner._appointments) == 1
    assert state.backend.log == ["create #0 applied, answer lost", f"lookup {state.outcomes[-1].appointment.request_id}"]


async def test_lost_answer_before_apply_is_resent_once_with_the_same_id():
    ctx, state, inner = make({"lose_before": {0}})
    said = await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    assert said.startswith("You're booked")
    log = state.backend.log
    assert log[0] == "create #0 lost before apply" and log[1].startswith("lookup") and log[2] == "create #1 ok"
    assert len(inner._appointments) == 1


async def test_two_lost_answers_hand_off_instead_of_looping():
    ctx, state, inner = make({"lose_before": {0, 1}})
    said = await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    assert "wasn't able to confirm" in said and "won't keep trying" in said
    assert state.outcomes[-1].effect.value == "unconfirmed"
    assert state.backend.calls == 2  # exactly one resend, then stop
    assert len(inner._appointments) == 0


async def test_refusal_text_tells_the_model_nothing_was_booked():
    ctx, state, inner = make()
    said = await book_appointment(ctx, name="Dana", date="2026-03-12", time="23:00", party_size=2)
    assert "Nothing was booked" in said
    assert state.outcomes[-1].effect.value == "nothing_changed"
    assert inner._appointments == {}


async def test_change_and_cancel_through_the_tools():
    ctx, state, inner = make()
    await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    code = state.outcomes[-1].appointment.id
    assert "now on March 13 at 10 AM" in await change_appointment(ctx, confirmation_code=code.upper(), date="2026-03-13", time="10:00")
    assert "no charge" in await cancel_appointment(ctx, confirmation_code=code)
    assert all(a.id in inner._cancelled for a in inner._appointments.values())
