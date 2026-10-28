import json
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace

from agent.tools import CallState, book_appointment, cancel_appointment, check_availability
from agent.transcript import TranscriptLog
from booking.flaky import FlakyService
from booking.service import BookingService

NOW = datetime(2026, 3, 10, 9, 0)
PHONE = "+15550100"
KEYS = {"ts", "call_id", "tool", "request_id", "code", "effect", "latency_ms", "handoff_id", "said"}


def make(path=None, flaky: dict | None = None):
    inner = BookingService(clock=lambda: NOW)
    backend = FlakyService(inner, **flaky) if flaky else inner
    transcript = TranscriptLog(path=path, clock=lambda: datetime(2026, 3, 10, 9, 0, 5, tzinfo=timezone.utc))
    state = CallState(backend=backend, caller_phone=PHONE, call_id="call_test", transcript=transcript)
    return SimpleNamespace(userdata=state), state


async def test_every_tool_call_writes_one_entry_with_the_fixed_keys():
    ctx, state = make()
    await check_availability(ctx, date="2026-03-12")
    await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    entries = state.transcript.entries
    assert [e.tool for e in entries] == ["check_availability", "book_appointment"]
    assert all(set(asdict(e)) == KEYS for e in entries)
    read, write = entries
    assert (read.request_id, read.code, read.effect, read.handoff_id) == (None, None, None, None)
    assert read.said.startswith("On March 12 we have")
    assert write.request_id.startswith("req_") and (write.code, write.effect) == ("booked", "booked")
    assert write.said.startswith("You're booked for 2 on March 12 at 2 PM")
    assert all(e.call_id == "call_test" and e.ts == "2026-03-10T09:00:05+00:00" and e.latency_ms >= 0 for e in entries)


async def test_a_retry_is_two_lines_with_one_request_id():
    ctx, state = make()
    await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    first, second = state.transcript.entries
    assert first.request_id == second.request_id
    assert first.said == second.said and second.code == "booked"


async def test_a_refusal_logs_its_reason_code():
    ctx, state = make()
    await book_appointment(ctx, name="Dana", date="2026-03-12", time="23:00", party_size=2)
    entry = state.transcript.entries[-1]
    assert (entry.code, entry.effect) == ("outside_hours", "nothing_changed")
    assert "Nothing was booked" in entry.said and entry.request_id.startswith("req_")


async def test_unconfirmed_logs_the_handoff_id():
    ctx, state = make(flaky={"lose_before": {0, 1}})
    await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    entry = state.transcript.entries[-1]
    assert (entry.code, entry.effect) == ("unconfirmed", "unconfirmed")
    assert entry.handoff_id == state.handoffs.open_items()[0].id


async def test_file_mode_appends_json_lines_that_read_back(tmp_path):
    path = tmp_path / "calls" / "call_test.jsonl"
    ctx, state = make(path=path)
    await check_availability(ctx, date="2026-03-12")
    await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["tool"] for line in lines] == ["check_availability", "book_appointment"]
    assert TranscriptLog.read(path) == state.transcript.entries


async def test_the_callers_number_never_appears_in_the_transcript(tmp_path):
    path = tmp_path / "call_test.jsonl"
    ctx, state = make(path=path)
    await book_appointment(ctx, name="Dana", date="2026-03-12", time="14:00", party_size=2)
    await cancel_appointment(ctx, confirmation_code=state.outcomes[-1].appointment.id)
    text = path.read_text(encoding="utf-8")
    assert PHONE not in text and "Dana" not in text
    assert [json.loads(line)["code"] for line in text.splitlines()] == ["booked", "cancelled"]
