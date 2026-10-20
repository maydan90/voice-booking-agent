# voice-booking-agent: briefing for coding agents

Read this before changing anything. It stays short on purpose; depth lives in module docstrings and the README.

This is a small LiveKit Agents voice agent that books, moves and cancels appointments over the phone. It exists to show a tool contract that production voice agents need. Every change must keep that contract intact.

## The contract (never break these)

1. **The caller hears the truth.** A tool returns an outcome (`booking/outcomes.py`) with a `code`, an `effect` and a `speakable` sentence. It never raises to the model, never returns `None`, never returns an error string. If a new situation has no outcome yet, add one.
2. **Decisions live in code, not in the prompt.** Hours, lead time, horizon, party size, card rules and fees are in `booking/policy.py` and enforced by the tools. The prompt may describe a rule; it never enforces one. Do not add rules to `agent/prompts.py`.
3. **Every write carries a per-change request id.** Ids come from `RequestIds.for_change(intent, **args)` and are a function of the change, not the attempt. Never generate an id per call, never reuse one for a different payload.
4. **Unknown is a state.** A `LostAnswer` is neither success nor failure: look up by request id, resend once with the same id, then return `Unconfirmed` and open one handoff. Never loop, never treat a timeout as a failure, never write again for a change that already has a handoff.
5. **Speech is short and ends with the effect.** Refusals end with "Nothing was booked." or "Nothing was changed." so the model stops retrying. One or two sentences, read aloud by TTS: no symbols, no lists, codes spelled letter by letter.

## Principles

- **No shortcuts.** No `type: ignore` without a one-line reason, no stubs, no `TODO`, no bare `except`, no `assert` as control flow.
- **Finish the job.** A change touches its tests, its eval scenario, the `RefusalCode` literal and `_REFUSAL_TEXT` when speech changes, and the README when the contract changes.
- **Readability first.** Plain dataclasses, frozen outcomes, docstrings that say why. The next reader is a person debugging a call.
- **Follow existing patterns.** Look at how `book_appointment` does it before adding a tool. Look at an existing scenario before writing one.
- **Minimal scope.** Change what the task needs. Fix an adjacent problem only when it is small and you say so.
- **Leave it better.** A wrong comment or a misleading name you walked past is yours to fix.

## Behavior

- **Ask when unclear.** If a request conflicts with the contract, say so and ask. Do not shoehorn.
- **Providers stay in `agent/main.py`.** Nothing else imports a speech or model provider. Do not add dependencies without asking.
- **Git.** Commit or push only when asked. One change per commit, imperative subject, no amending pushed commits.
- **No employer code or data, ever.** The domain is a generic studio. Phone numbers in tests and evals are `+1555…` placeholders. Names are placeholders.

## Commands

```bash
pip install -e ".[dev]"
pytest -q                    # unit tests, no keys needed
python -m evals.run_evals    # scripted tool-call scenarios, no LLM
pip install -e ".[voice]"    # only to talk to it
python -m agent.main console # microphone and speaker
```

Python 3.10 or newer. If the system Python is older, `uv venv --python 3.12 .venv` and use `.venv/bin/python`.

CI runs `pytest -q` and `python -m evals.run_evals` on every push and pull request. Both must pass before you say a change is done.

## Build and verify

1. Read the module you are touching and its test file. `tests/` mirrors `booking/` and `agent/`.
2. Write the test first when the change is a rule or an outcome.
3. If the caller would hear something different, add or update a scenario in `evals/scenarios.json`. Scenarios assert on what the caller hears and on what the backend holds afterwards, not on internals.
4. Run both commands. Paste the summary line of each in your final message.
5. Describe the change in terms of the caller: what they hear now that they did not before.

## Where to look

| Topic | File |
|---|---|
| Outcome types, speech helpers | `booking/outcomes.py` |
| Rules the model cannot negotiate | `booking/policy.py` |
| In-memory backend, idempotency, replay, capacity | `booking/service.py` |
| Losing answers on purpose (tests, evals) | `booking/flaky.py` |
| One handoff per unconfirmed change | `booking/handoffs.py` |
| "next Tuesday at two" to ISO, against the call's clock | `booking/spoken_dates.py` |
| The tools, recovery path, per-call state | `agent/tools.py` |
| Per-change request ids | `agent/request_ids.py` |
| System prompt (short on purpose) | `agent/prompts.py` |
| LiveKit entrypoint, the only file with providers | `agent/main.py` |
| Scenarios and the runner | `evals/scenarios.json`, `evals/run_evals.py` |

## Looks like a bug, is not

- `BookingService._replay` raises `ValueError` when a request id comes back with a different payload. Deliberate: that is a caller bug, and replaying would hide it.
- The model calling the same tool twice with the same arguments is expected. Request ids make it harmless. Do not "fix" it by blocking repeats.
- The confirmation-code alphabet has no `0/o` or `1/i/l`. They get misheard on the phone.
- `availability()` returns `[]` for a date it cannot parse instead of raising. The tool turns that into a sentence.
- The prompt is short. That is the point; see rule 2.

## Done means

- `pytest -q` and `python -m evals.run_evals` pass.
- New behavior has a test and, if audible, a scenario.
- README updated if the contract changed.
- No provider or dependency changes unless asked.
- Nothing from any employer.
