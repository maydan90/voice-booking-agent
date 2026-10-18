"""Turning what a caller said into a date and a time, without an NLP library.

The tools ask the model for YYYY-MM-DD and HH:MM, and most of the time that is
what they get. But what the model extracts from speech is often "the 12th",
"next Tuesday" or "tomorrow at two", and if the model resolves those itself the
answer depends on what it believes today's date is. This module resolves them
against the call's clock, deterministically, so a tool can accept the phrase
and the code decides what it means.

Deliberately small. It covers the phrases callers actually use for a booking
a few days out and returns None for everything else, in which case the agent
asks the caller to repeat the date rather than guessing.

Conventions, since spoken dates are ambiguous:

- A bare weekday ("Tuesday", "this Tuesday") is the first one after today.
- "next Tuesday" is the Tuesday of the following week (after the coming Sunday).
- "the 12th" is the next 12th on or after today, rolling into next month.
- "March 12" without a year is the next March 12 on or after today.
- A bare hour without AM or PM is read as a business hour: 9 to 12 mean
  morning and noon, 1 to 8 mean afternoon and evening. "Two" is 14:00.

The agent reads every resolved date back to the caller before booking, so a
wrong guess costs one exchange, never a wrong appointment.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = {
    name: i + 1
    for i, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sept", "sep"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ]
    )
    for name in names
}
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
}
_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
    "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "twenty-first": 21, "twenty-second": 22, "twenty-third": 23, "twenty-fourth": 24,
    "twenty-fifth": 25, "twenty-sixth": 26, "twenty-seventh": 27, "twenty-eighth": 28, "twenty-ninth": 29,
    "thirtieth": 30, "thirty-first": 31,
}


def _clean(text: str) -> str:
    text = text.strip().lower().replace(",", " ")
    text = re.sub(r"\b(on|at|for|the|of|o'clock|oclock)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _day_of_month(token: str) -> int | None:
    """'12th', '12', 'twelfth' -> 12."""
    m = re.fullmatch(r"(\d{1,2})(st|nd|rd|th)?", token)
    if m:
        return int(m.group(1))
    return _ORDINAL_WORDS.get(token)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _next_day_of_month(day: int, today: date) -> date | None:
    """The next date with this day number, today included; skips months without it."""
    year, month = today.year, today.month
    for _ in range(13):
        candidate = _safe_date(year, month, day)
        if candidate is not None and candidate >= today:
            return candidate
        month += 1
        if month == 13:
            month, year = 1, year + 1
    return None


def _next_weekday(weekday: int, today: date, following_week: bool) -> date:
    days_ahead = (weekday - today.weekday()) % 7 or 7  # bare weekday: strictly after today
    if following_week:
        # "next Tuesday": the Tuesday of the week after this one.
        days_to_next_monday = 7 - today.weekday()
        return today + timedelta(days=days_to_next_monday + weekday)
    return today + timedelta(days=days_ahead)


def parse_spoken_date(text: str, now: datetime) -> str | None:
    """Resolves a spoken date to YYYY-MM-DD relative to `now`, or None."""
    today = now.date()
    s = _clean(text)
    if not s:
        return None

    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if iso:
        d = _safe_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        return d.isoformat() if d else None

    if s == "today":
        return today.isoformat()
    if s == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if s in ("day after tomorrow", "day after"):
        return (today + timedelta(days=2)).isoformat()

    m = re.fullmatch(r"in (a|an|\w+) (day|days|week|weeks)", s)
    if m:
        n = 1 if m.group(1) in ("a", "an") else _NUMBER_WORDS.get(m.group(1))
        if n is None and m.group(1).isdigit():
            n = int(m.group(1))
        if n is None:
            return None
        days = n * (7 if m.group(2).startswith("week") else 1)
        return (today + timedelta(days=days)).isoformat()

    m = re.fullmatch(r"(this|next)? ?(\w+day)", s)
    if m and m.group(2) in _WEEKDAYS:
        weekday = _WEEKDAYS.index(m.group(2))
        return _next_weekday(weekday, today, following_week=m.group(1) == "next").isoformat()

    # "march 12", "march 12th", "12 march", "12th march", "march 12 2027", "12th of march" (the "of" is stripped)
    m = re.fullmatch(r"(\w+) (\w+(?:-\w+)?)(?: (\d{4}))?", s)
    if m:
        a, b, year = m.group(1), m.group(2), m.group(3)
        month = _MONTHS.get(a)
        day = _day_of_month(b)
        if month is None:
            month, day = _MONTHS.get(b), _day_of_month(a)
        if month is not None and day is not None:
            if year:
                d = _safe_date(int(year), month, day)
                return d.isoformat() if d else None
            d = _safe_date(today.year, month, day)
            if d is None or d < today:
                d = _safe_date(today.year + 1, month, day)
            return d.isoformat() if d else None

    # "3/12" or "3/12/2026", month first (US callers)
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", s)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if m.group(3):
            d = _safe_date(int(m.group(3)), month, day)
            return d.isoformat() if d else None
        d = _safe_date(today.year, month, day)
        if d is None or d < today:
            d = _safe_date(today.year + 1, month, day)
        return d.isoformat() if d else None

    # "12th", "twelfth", "12"
    day = _day_of_month(s)
    if day is not None and 1 <= day <= 31:
        d = _next_day_of_month(day, today)
        return d.isoformat() if d else None

    return None


def parse_spoken_time(text: str, morning_from: int = 9) -> str | None:
    """Resolves a spoken time to HH:MM, or None.

    Hours without AM or PM are read as business hours: `morning_from` to 12
    are taken literally, anything smaller is afternoon. With the default,
    "two" is 14:00 and "ten" is 10:00.
    """
    s = _clean(text)
    s = s.replace(".", "")
    s = re.sub(r"(\d)(am|pm)\b", r"\1 \2", s)  # "2pm" -> "2 pm"
    if not s:
        return None
    if s in ("noon", "midday", "twelve noon", "12 noon"):
        return "12:00"
    if s == "midnight":
        return "00:00"

    suffix = None
    m = re.search(r"\b(am|pm|a m|p m|in morning|morning|in afternoon|afternoon|in evening|evening|tonight)\b", s)
    if m:
        word = m.group(1)
        suffix = "am" if word in ("am", "a m", "in morning", "morning") else "pm"
        s = (s[: m.start()] + s[m.end() :]).strip()

    hour: int | None = None
    minute = 0

    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", s)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
    else:
        m = re.fullmatch(r"half past (\w+)", s)
        if m:
            hour, minute = _NUMBER_WORDS.get(m.group(1)), 30
        else:
            m = re.fullmatch(r"quarter past (\w+)", s)
            if m:
                hour, minute = _NUMBER_WORDS.get(m.group(1)), 15
            else:
                m = re.fullmatch(r"quarter to (\w+)", s)
                if m:
                    h = _NUMBER_WORDS.get(m.group(1))
                    hour, minute = ((h - 1) if h else None), 45
                else:
                    # "two", "two thirty", "two fifteen", "twenty past two" is left out on purpose
                    m = re.fullmatch(r"(\w+)(?: (\w+))?", s)
                    if m and m.group(1) in _NUMBER_WORDS:
                        hour = _NUMBER_WORDS[m.group(1)]
                        if m.group(2):
                            minute_word = _NUMBER_WORDS.get(m.group(2))
                            if minute_word is None:
                                return None
                            minute = minute_word

    if hour is None or not (0 <= hour <= 23) or not (0 <= minute <= 59):
        return None
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    elif suffix is None and 1 <= hour < morning_from:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def parse_spoken_when(text: str, now: datetime, morning_from: int = 9) -> tuple[str | None, str | None]:
    """Splits "tomorrow at two" into a date and a time; either half may be None.

    Accepts "<date> at <time>", "<date> <time>" when the tail parses as a time,
    "<time> <date>" the other way round, or a date or time alone.
    """
    s = text.strip().lower().replace(",", " ")
    s = re.sub(r"\s+", " ", s)
    if not s:
        return None, None

    if " at " in s:
        left, right = s.split(" at ", 1)
        return parse_spoken_date(left, now), parse_spoken_time(right, morning_from)

    words = s.split(" ")
    # Try every split point: the longest date prefix with a time suffix, then the reverse.
    for i in range(len(words) - 1, 0, -1):
        d, t = parse_spoken_date(" ".join(words[:i]), now), parse_spoken_time(" ".join(words[i:]), morning_from)
        if d is not None and t is not None:
            return d, t
    for i in range(1, len(words)):
        t, d = parse_spoken_time(" ".join(words[:i]), morning_from), parse_spoken_date(" ".join(words[i:]), now)
        if d is not None and t is not None:
            return d, t

    d = parse_spoken_date(s, now)
    if d is not None:
        return d, None
    return None, parse_spoken_time(s, morning_from)


__all__ = ["parse_spoken_date", "parse_spoken_time", "parse_spoken_when"]
