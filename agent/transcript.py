"""One JSON line per tool outcome: the raw material for evals on real calls.

The replayed evals in `evals/` script the tool calls. On a real call the model
makes them, and the only durable record of what the tools said back is this
transcript: one line per tool call with the request id, the outcome code and
effect, the latency and the sentence the model was told to say. A transcript
eval reads those lines later (did the model relay the sentence, did it stop
after "nothing was booked"), and a person debugging a call reads them first.

Never the caller's phone number and never their name: the request id is
enough to find the change in the backend, and the sentence the agent said
already carries what the caller heard.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class TranscriptEntry:
    ts: str  # ISO 8601 with an offset, from the log's clock
    call_id: str
    tool: str
    request_id: str | None  # None for a read such as check_availability
    code: str | None  # the outcome code ("booked", a refusal code, "unconfirmed"); None for a read
    effect: str | None  # booked, changed, cancelled, nothing_changed, unconfirmed; None for a read
    latency_ms: int
    handoff_id: str | None  # set when the change is with a person
    said: str  # the sentence returned to the model, verbatim


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TranscriptLog:
    """Keeps every entry in memory and, when given a path, appends each one to that .jsonl file as it happens."""

    def __init__(self, path: Path | None = None, clock: Callable[[], datetime] = _utc_now) -> None:
        self._path = path
        self._clock = clock
        self.entries: list[TranscriptEntry] = []

    def record(
        self,
        *,
        call_id: str,
        tool: str,
        request_id: str | None,
        code: str | None,
        effect: str | None,
        latency_ms: int,
        handoff_id: str | None,
        said: str,
    ) -> TranscriptEntry:
        entry = TranscriptEntry(
            ts=self._clock().isoformat(),
            call_id=call_id,
            tool=tool,
            request_id=request_id,
            code=code,
            effect=effect,
            latency_ms=latency_ms,
            handoff_id=handoff_id,
            said=said,
        )
        self.entries.append(entry)
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        return entry

    @staticmethod
    def read(path: Path) -> list[TranscriptEntry]:
        """Reads a transcript back; the inverse of `record` with a path."""
        lines = path.read_text(encoding="utf-8").splitlines()
        return [TranscriptEntry(**json.loads(line)) for line in lines if line.strip()]


__all__ = ["TranscriptEntry", "TranscriptLog"]
