from datetime import datetime

import pytest

from booking.spoken_dates import parse_spoken_date, parse_spoken_time, parse_spoken_when

# Tuesday, March 10, 2026, 9 AM: the same clock the service tests use.
NOW = datetime(2026, 3, 10, 9, 0)


@pytest.mark.parametrize(
    "said, expected",
    [
        # relative days
        ("today", "2026-03-10"),
        ("tomorrow", "2026-03-11"),
        ("the day after tomorrow", "2026-03-12"),
        ("in two days", "2026-03-12"),
        ("in 3 days", "2026-03-13"),
        ("in a week", "2026-03-17"),
        ("in two weeks", "2026-03-24"),
        # weekdays: bare means the first one after today; "next" means the following week
        ("tuesday", "2026-03-17"),  # today is Tuesday, so a week out, not today
        ("wednesday", "2026-03-11"),
        ("on friday", "2026-03-13"),
        ("this friday", "2026-03-13"),
        ("next friday", "2026-03-20"),
        ("next monday", "2026-03-16"),
        ("sunday", "2026-03-15"),
        ("next sunday", "2026-03-22"),
        # day of month: next occurrence, rolling into the next month
        ("the 12th", "2026-03-12"),
        ("12th", "2026-03-12"),
        ("the twelfth", "2026-03-12"),
        ("the 10th", "2026-03-10"),  # today counts
        ("the 3rd", "2026-04-03"),  # already passed this month
        ("the 31st", "2026-03-31"),
        ("the 30th", "2026-03-30"),
        # month and day, in either order, with or without a year
        ("march 12", "2026-03-12"),
        ("march 12th", "2026-03-12"),
        ("12 march", "2026-03-12"),
        ("the 12th of march", "2026-03-12"),
        ("february 2", "2027-02-02"),  # passed this year
        ("march 12 2027", "2027-03-12"),
        ("sept 1", "2026-09-01"),
        # numeric and ISO pass through
        ("3/12", "2026-03-12"),
        ("3/12/2027", "2027-03-12"),
        ("2026-03-12", "2026-03-12"),
        # not a date
        ("whenever", None),
        ("the 32nd", None),
        ("february 30", None),
        ("2026-13-01", None),
        ("", None),
    ],
)
def test_spoken_dates(said, expected):
    assert parse_spoken_date(said, NOW) == expected


@pytest.mark.parametrize(
    "said, expected",
    [
        # explicit am/pm
        ("2pm", "14:00"),
        ("2 pm", "14:00"),
        ("2 p.m.", "14:00"),
        ("two pm", "14:00"),
        ("10am", "10:00"),
        ("10 in the morning", "10:00"),
        ("two in the afternoon", "14:00"),
        ("12 pm", "12:00"),
        ("12 am", "00:00"),
        ("six in the evening", "18:00"),
        # no am/pm: business-hours reading
        ("two", "14:00"),
        ("at two", "14:00"),
        ("2", "14:00"),
        ("two o'clock", "14:00"),
        ("nine", "09:00"),
        ("ten", "10:00"),
        ("eleven", "11:00"),
        ("twelve", "12:00"),
        ("four", "16:00"),
        ("eight", "20:00"),
        # minutes
        ("2:30", "14:30"),
        ("2:30 pm", "14:30"),
        ("two thirty", "14:30"),
        ("half past two", "14:30"),
        ("quarter past ten", "10:15"),
        ("quarter to three", "14:45"),
        ("10:05", "10:05"),
        # words for the clock
        ("noon", "12:00"),
        ("midday", "12:00"),
        ("midnight", "00:00"),
        # not a time
        ("sometime", None),
        ("25", None),
        ("two seventy", None),
        ("", None),
    ],
)
def test_spoken_times(said, expected):
    assert parse_spoken_time(said) == expected


def test_morning_from_changes_the_bare_hour_reading():
    # A venue that opens at 7 reads "seven" and "eight" as the morning.
    assert parse_spoken_time("seven", morning_from=7) == "07:00"
    assert parse_spoken_time("eight", morning_from=7) == "08:00"
    assert parse_spoken_time("six", morning_from=7) == "18:00"


@pytest.mark.parametrize(
    "said, expected",
    [
        ("tomorrow at two", ("2026-03-11", "14:00")),
        ("next tuesday at 10am", ("2026-03-17", "10:00")),
        ("the 12th at half past two", ("2026-03-12", "14:30")),
        ("march 12 at noon", ("2026-03-12", "12:00")),
        ("friday 3pm", ("2026-03-13", "15:00")),
        ("3pm friday", ("2026-03-13", "15:00")),
        ("two on the twelfth", ("2026-03-12", "14:00")),
        ("tomorrow", ("2026-03-11", None)),
        ("at four", (None, "16:00")),
        ("the 12th at sometime", ("2026-03-12", None)),
        ("", (None, None)),
    ],
)
def test_spoken_when(said, expected):
    assert parse_spoken_when(said, NOW) == expected
