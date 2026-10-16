"""Per-change request ids, reused on retry.

The model decides to call `book_appointment(...)`. If the first attempt's
answer is lost, or the model simply calls the tool again with the same
arguments (models do this), the backend must see the same request id so it
can deduplicate. The id is therefore a function of the *change*, not of the
attempt: same intent + same arguments within one call = same id.

A different change (new time, new party size) gets a new id, so a legitimate
second booking in the same call is still possible.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field


@dataclass
class RequestIds:
    session_salt: str = field(default_factory=lambda: secrets.token_hex(8))
    _ids: dict[str, str] = field(default_factory=dict)

    def for_change(self, intent: str, **args: object) -> str:
        key = intent + "|" + "|".join(f"{k}={args[k]}" for k in sorted(args))
        if key not in self._ids:
            digest = hashlib.sha256(f"{self.session_salt}|{key}".encode()).hexdigest()[:16]
            self._ids[key] = f"req_{digest}"
        return self._ids[key]
