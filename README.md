# voice-booking-agent

A small voice agent on [LiveKit Agents](https://github.com/livekit/agents) that books, moves and cancels appointments over the phone. It exists to show four things I think every production voice agent needs, and that are easy to get wrong:

1. **Typed, speakable tool outcomes.** Tools never return an error to the model. They return an outcome with a `code`, an `effect` (booked, nothing changed, unconfirmed) and a `speakable` sentence the agent says verbatim. "That time is full. I do have 3 PM, 4 PM. Nothing was booked yet." ends the retry loop that a raw 400 starts.
2. **Per-change request ids, reused on retry.** The id is a function of the change (intent + arguments within one call), not of the attempt. When the model calls `book_appointment` twice with the same details, or when this code resends after a lost answer, the backend sees the same id and dedupes. One appointment, one confirmation code.
3. **Recovery of lost answers, bounded.** A timeout is neither success nor failure. The tool looks the request id up first; if the write applied, that is the outcome and nothing is resent. If it did not, it resends once with the same id. If that is lost too, the caller hears "I wasn't able to confirm that... I won't keep trying on my own" and the call is handed off. Never a double booking, never an infinite loop.
4. **Decisions that never reach the model.** Hours, lead time, horizon, party size, card requirements and cancellation fees live in `booking/policy.py` and are enforced by the tools. The prompt tells the model these rules exist; the code makes them true. A caller cannot talk the agent into 11 PM.

The evals replay scripted tool-call sequences through the real tools, against a backend that can lose answers on purpose, and assert on what the caller would hear and on what the backend holds afterwards. No LLM is involved, so they are deterministic and run in CI in about a second.

## Run it

```bash
pip install -e ".[dev]"
pytest -q                    # 121 tests, no keys needed
python -m evals.run_evals    # 9 scripted scenarios, no keys needed
```

To talk to it:

```bash
pip install -e ".[voice]"
cp .env.example .env         # OPENAI_API_KEY, DEEPGRAM_API_KEY, CARTESIA_API_KEY
python -m agent.main console # microphone and speaker in your terminal
python -m agent.main dev     # join a LiveKit room (also needs LIVEKIT_URL / API key / secret)
```

The pipeline is Deepgram Nova-3 for speech-to-text, GPT-4o-mini as the model, Cartesia for text-to-speech, Silero for voice activity detection. Swapping any of them is one line in `agent/main.py`; nothing else in the repo imports a provider.

## How a booking flows

```
caller: "Table for two on the 12th at two"
model  ──▶ book_appointment(name, date, time, party_size)
tool   ──▶ request_id = RequestIds.for_change("create", ...)      # stable for this change
       ──▶ backend.create(request_id, ...)
              │
              ├── Booked          ──▶ "You're booked for 2 on March 12 at 2 PM. Your confirmation code is K 7 M 2 P."
              ├── Refused         ──▶ "That time is full. I do have 3 PM, 4 PM. Nothing was booked yet."
              └── LostAnswer      ──▶ backend.lookup(request_id)
                                        ├── found     ──▶ the applied outcome, no second write
                                        └── not found ──▶ backend.create(request_id, ...) once more
                                                            └── LostAnswer again ──▶ Unconfirmed, hand off
model  ──▶ says the sentence to the caller, verbatim
```

## Layout

```
booking/
  outcomes.py    Booked / Changed / Cancelled / Refused / Unconfirmed, each with .effect and .speakable
  policy.py      the rules the model cannot negotiate
  service.py     in-memory backend: idempotent on request_id, lookup(request_id), capacity per slot
  flaky.py       wrapper that loses answers before or after the write, for tests and evals
  handoffs.py    one handoff per unconfirmed change (request id, caller's number, intent, details), idempotent on the request id
  spoken_dates.py "next Tuesday", "the 12th", "tomorrow at two" -> YYYY-MM-DD and HH:MM, resolved against the call's clock
agent/
  request_ids.py per-change ids, reused on retry
  tools.py       the four function tools; recovery path; per-call state in AgentSession.userdata
  prompts.py     the system prompt (short; the rules live in code)
  main.py        LiveKit entrypoint; the only file that knows about STT/LLM/TTS providers
evals/
  scenarios.json scripted tool-call sequences with backend conditions and expected speech
  run_evals.py   replays them through the real tools
tests/           service, tools and eval scenarios under pytest
```

## Details worth noticing

- Confirmation codes use an alphabet without `0/o` and `1/i/l`, and the agent reads them letter by letter, because TTS will otherwise invent a word and STT will otherwise mishear the readback.
- Change and cancel are scoped to the caller's phone number (from the SIP participant on a real call). Someone else's code gets "That appointment is under a different phone number", not a lookup leak.
- The same request id with a different payload raises in the backend rather than replaying. That is a bug in the caller, and a replay would hide it.
- `Unconfirmed` is the only outcome whose text promises a human callback, so it opens a handoff in `booking/handoffs.py` with what a person needs to act without replaying the call: the request id, the caller's number, the intent and the change's details. It is created once per change. If the model repeats the same tool call after hearing "I won't keep trying", the tool writes nothing, opens no second ticket and says the same sentence again, because an agent write racing a person's callback is how a booking gets made twice. The queue is in memory here; in a real deployment it points at the ticketing or CRM system with the same idempotency on the request id. Tests in `tests/test_handoffs.py`.
- Spoken dates are resolved in code, not by the model. `booking/spoken_dates.py` turns "next Tuesday", "the 12th" and "tomorrow at two" into ISO values against the call's clock, with the conventions written down (a bare weekday is the next one, "next" means the following week, a bare "two" is 2 PM during business hours). Anything it cannot resolve returns None and the agent asks again, since a model guessing today's date is how a booking lands on the wrong day. Table-driven tests in `tests/test_spoken_dates.py`.
- The system prompt is deliberately short. Everything the agent must not do is enforced by a tool, so the prompt only has to explain how to relay what the tools say.

## What this is not

It is a demonstration of the tool and backend contract, not a product: the backend is in memory, there is no tenant model, no telephony wiring beyond reading the SIP phone number, and no transcript evals against real audio. The domain (a studio taking one-hour appointments) is generic and nothing here is taken from an employer's codebase.

MIT licensed. Written by [Yurii Maidaniuk](https://github.com/maydan90).
