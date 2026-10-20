"""Replayed-turn evals without an LLM.

Each scenario in scenarios.json is a sequence of tool calls the model would
make, with the exact backend conditions (a full slot, a lost answer) set up
in code. We replay the calls through the real tools and assert on what the
agent would say and on what is true in the backend afterwards.

This tests the part of a voice agent that must be deterministic. Whether the
model chooses the right tool from speech is a separate, non-deterministic
eval that belongs with real transcripts; this suite makes sure that once it
does, the caller hears the truth and the backend holds exactly one booking.

    python -m evals.run_evals
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent.tools import ALL_TOOLS, CallState
from booking.flaky import FlakyService
from booking.service import BookingService

TOOLS = {t.info.name if hasattr(t, "info") else t.__name__: t for t in ALL_TOOLS}
CALLER = "+15550100"
OTHER = "+15550199"


def build(scenario: dict[str, Any]) -> tuple[CallState, Any, dict[str, str]]:
    now = datetime.fromisoformat(scenario["now"])
    inner = BookingService(clock=lambda: now)
    codes: dict[str, str] = {}

    setup = scenario.get("setup", {})
    if "fill_slot" in setup:
        s = setup["fill_slot"]
        for i in range(inner.capacity):
            inner.create(f"seed-{i}", f"Seed {i}", OTHER, s["date"], s["time"], 1)
    if "other_booking" in setup:
        s = setup["other_booking"]
        out = inner.create("seed-other", "Someone Else", OTHER, s["date"], s["time"], 1)
        codes["$OTHER_CODE"] = out.appointment.id  # type: ignore[union-attr]

    flaky = scenario.get("flaky")
    backend: Any = (
        FlakyService(inner, lose_before=set(flaky.get("lose_before", [])), lose_after=set(flaky.get("lose_after", [])))
        if flaky
        else inner
    )
    state = CallState(backend=backend, caller_phone=CALLER)
    return state, inner, codes


async def run_scenario(scenario: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    state, inner, codes = build(scenario)
    context = SimpleNamespace(userdata=state)
    said: list[str] = []

    for i, step in enumerate(scenario["steps"]):
        args = {k: codes.get(v, v) if isinstance(v, str) else v for k, v in step["args"].items()}
        text = await TOOLS[step["tool"]](context, **args)
        said.append(text)
        # Capture the confirmation code the first booking produced, for later steps.
        if state.outcomes and state.outcomes[-1].code == "booked" and "$CODE" not in codes:
            codes["$CODE"] = state.outcomes[-1].appointment.id  # type: ignore[union-attr]

        for needle in step.get("expect_contains", []):
            if needle not in text:
                failures.append(f"step {i}: expected {needle!r} in {text!r}")
        if "expect_effect" in step:
            got = state.outcomes[-1].effect.value if state.outcomes else "(none)"
            if got != step["expect_effect"]:
                failures.append(f"step {i}: expected effect {step['expect_effect']}, got {got}")
        if "expect_same_text_as_step" in step and text != said[step["expect_same_text_as_step"]]:
            failures.append(f"step {i}: expected the same reply as step {step['expect_same_text_as_step']}")

    live = [a for a in inner._appointments.values() if a.phone == CALLER and a.id not in inner._cancelled]
    if "expect_appointments" in scenario and len(live) != scenario["expect_appointments"]:
        failures.append(f"expected the caller to hold {scenario['expect_appointments']} appointments, found {len(live)}")
    if "expect_backend_writes" in scenario:
        writes = getattr(state.backend, "calls", None)
        if writes != scenario["expect_backend_writes"]:
            failures.append(f"expected {scenario['expect_backend_writes']} backend writes, saw {writes}")
    if "expect_handoffs" in scenario:
        opened = len(state.handoffs.open_items())
        if opened != scenario["expect_handoffs"]:
            failures.append(f"expected {scenario['expect_handoffs']} handoffs, found {opened}")
    if "expect_backend_log" in scenario:
        log = getattr(state.backend, "log", [])
        for needle in scenario["expect_backend_log"]:
            if not any(needle in line for line in log):
                failures.append(f"expected backend log entry containing {needle!r}, log was {log}")
    return failures


async def main() -> int:
    scenarios = json.loads((Path(__file__).parent / "scenarios.json").read_text())
    total_failures = 0
    for scenario in scenarios:
        failures = await run_scenario(scenario)
        status = "PASS" if not failures else "FAIL"
        print(f"[{status}] {scenario['name']}")
        for f in failures:
            print(f"        {f}")
        total_failures += len(failures)
    print(f"\n{len(scenarios)} scenarios, {total_failures} failures")
    return 1 if total_failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
