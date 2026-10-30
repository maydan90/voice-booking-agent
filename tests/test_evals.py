import json
from pathlib import Path

import pytest

from evals.run_evals import main, run_scenario

SCENARIOS = json.loads((Path(__file__).parent.parent / "evals" / "scenarios.json").read_text())


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
async def test_scenario(scenario):
    failures = await run_scenario(scenario)
    assert failures == []


LOST_AFTER = next(s for s in SCENARIOS if s["name"].startswith("lost answer after"))


async def test_only_runs_matching_scenarios(capsys):
    assert await main(["--only", "LOST ANSWER"]) == 0
    out = capsys.readouterr().out
    assert "3 scenarios, 0 failures" in out
    assert "happy path" not in out


async def test_only_with_no_match_says_so_and_exits_2(capsys):
    assert await main(["--only", "no such scenario"]) == 2
    assert "no scenario name contains 'no such scenario'" in capsys.readouterr().out


async def test_verbose_prints_what_was_heard_and_the_backend_log_on_failure(capsys):
    broken = {**LOST_AFTER, "name": "broken on purpose", "expect_appointments": 2}
    assert await main(["-v"], scenarios=[broken]) == 1
    out = capsys.readouterr().out
    assert "heard 0: You're booked" in out
    assert "backend: create #0 applied, answer lost" in out


async def test_verbose_stays_quiet_when_scenarios_pass(capsys):
    assert await main(["-v"], scenarios=[LOST_AFTER]) == 0
    assert "backend:" not in capsys.readouterr().out
