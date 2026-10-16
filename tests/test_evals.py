import json
from pathlib import Path

import pytest

from evals.run_evals import run_scenario

SCENARIOS = json.loads((Path(__file__).parent.parent / "evals" / "scenarios.json").read_text())


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
async def test_scenario(scenario):
    failures = await run_scenario(scenario)
    assert failures == []
