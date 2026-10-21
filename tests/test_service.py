from datetime import datetime

import pytest

from booking.flaky import FlakyService
from booking.outcomes import Booked, Cancelled, Changed, Refused
from booking.service import BookingService, LostAnswer

NOW = datetime(2026, 3, 10, 9, 0)


def service() -> BookingService:
    return BookingService(clock=lambda: NOW)


def test_create_is_idempotent_on_request_id():
    s = service()
    a = s.create("req-1", "Dana", "+1555", "2026-03-12", "14:00", 2)
    b = s.create("req-1", "Dana", "+1555", "2026-03-12", "14:00", 2)
    assert isinstance(a, Booked) and isinstance(b, Booked)
    assert a.appointment.id == b.appointment.id
    assert len(s._appointments) == 1


def test_same_request_id_with_different_payload_is_a_bug_not_a_replay():
    s = service()
    s.create("req-1", "Dana", "+1555", "2026-03-12", "14:00", 2)
    with pytest.raises(ValueError):
        s.create("req-1", "Dana", "+1555", "2026-03-12", "15:00", 2)


def test_capacity_refusal_offers_alternatives_and_books_nothing():
    s = service()
    s.create("a", "A", "+1", "2026-03-12", "14:00", 1)
    s.create("b", "B", "+2", "2026-03-12", "14:00", 1)
    out = s.create("c", "C", "+3", "2026-03-12", "14:00", 1)
    assert isinstance(out, Refused)
    assert out.code == "slot_unavailable"
    assert "14:00" not in out.alternatives and "15:00" in out.alternatives
    assert "Nothing was booked" in out.speakable
    assert len(s._appointments) == 2


@pytest.mark.parametrize(
    "date,time,party,code",
    [
        ("2026-03-12", "23:00", 2, "outside_hours"),
        ("2026-03-12", "14:30", 2, "outside_hours"),
        ("2026-03-10", "10:00", 2, "too_soon"),
        ("2026-05-01", "10:00", 2, "too_far_ahead"),
        ("2026-03-12", "14:00", 5, "party_too_large"),
        ("2026-03-12", "14:00", 3, "card_required"),
        ("not-a-date", "14:00", 2, "invalid_date"),
    ],
)
def test_policy_refusals(date, time, party, code):
    out = service().create("r", "Dana", "+1555", date, time, party)
    assert isinstance(out, Refused) and out.code == code


def test_card_on_file_unlocks_groups():
    s = service()
    s.cards_on_file.add("+1555")
    assert isinstance(s.create("r", "Dana", "+1555", "2026-03-12", "14:00", 3), Booked)


def test_change_and_cancel_are_scoped_to_the_caller_and_idempotent():
    s = service()
    booked = s.create("c1", "Dana", "+1555", "2026-03-12", "14:00", 2)
    assert isinstance(booked, Booked)
    code = booked.appointment.id

    wrong = s.change("ch0", code, "+1999", "2026-03-13", "10:00")
    assert isinstance(wrong, Refused) and wrong.code == "wrong_caller"

    moved = s.change("ch1", code, "+1555", "2026-03-13", "10:00")
    assert isinstance(moved, Changed) and moved.appointment.time == "10:00"
    assert s.change("ch1", code, "+1555", "2026-03-13", "10:00") == moved

    cancelled = s.cancel("cx1", code, "+1555")
    assert isinstance(cancelled, Cancelled) and cancelled.fee_applies is False
    assert s.cancel("cx1", code, "+1555") == cancelled
    gone = s.cancel("cx2", code, "+1555")
    assert isinstance(gone, Refused) and gone.code == "not_found"


def test_late_cancellation_states_the_fee():
    s = service()
    booked = s.create("c1", "Dana", "+1555", "2026-03-10", "12:00", 2)
    assert isinstance(booked, Booked)
    out = s.cancel("cx", booked.appointment.id, "+1555")
    assert isinstance(out, Cancelled) and out.fee_applies
    assert "late cancellation fee" in out.speakable


def test_lookup_returns_the_applied_outcome_or_none():
    s = service()
    assert s.lookup("nope") is None
    booked = s.create("c1", "Dana", "+1555", "2026-03-12", "14:00", 2)
    assert s.lookup("c1") == booked


def test_flaky_service_can_apply_and_then_lose_the_answer():
    inner = service()
    flaky = FlakyService(inner, lose_after={0})
    with pytest.raises(LostAnswer):
        flaky.create("c1", "Dana", "+1555", "2026-03-12", "14:00", 2)
    assert inner.lookup("c1") is not None  # it applied
    assert flaky.log == ["create #0 applied, answer lost"]


def test_confirmation_codes_avoid_letters_that_get_misheard():
    s = service()
    for i in range(50):
        out = s.create(f"r{i}", "X", f"+{i}", "2026-03-12", f"{9 + i % 8:02d}:00", 1)
        if isinstance(out, Booked):
            assert not set(out.appointment.id) & set("01oil")


def test_every_refusal_code_has_speech_and_nothing_else_does():
    # refusal() passes a _REFUSAL_TEXT key where a RefusalCode is expected; this keeps the two in step.
    from typing import get_args

    from booking.outcomes import RefusalCode
    from booking.service import _REFUSAL_TEXT

    assert set(_REFUSAL_TEXT) == set(get_args(RefusalCode))
