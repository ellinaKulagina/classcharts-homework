"""One-way ClassCharts -> Google Calendar synchronization. Dry-run by default."""

import argparse
import hashlib
import os
import re
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .client import (
    ClassChartsError,
    ConfigurationError,
    Homework,
    StudentClient,
    _iso_date,
)
from .google_calendar import CalendarError, GoogleCalendar


def _zone(value):
    try:
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ConfigurationError(
            "CALENDAR_TIMEZONE must be a valid IANA timezone."
        ) from None


def _days(env, name, default):
    value = env.get(name, str(default))

    if not re.fullmatch(r"[0-9]{1,3}", value) or not 0 <= int(value) <= 365:
        raise ConfigurationError(
            name + " must be an integer from 0 to 365."
        )

    return int(value)


def date_window(env, *, from_date=None, to_date=None, now=None):
    zone = _zone(
        env.get(
            "CALENDAR_TIMEZONE",
            "Europe/London",
        )
    )

    if (from_date is None) != (to_date is None):
        raise ConfigurationError(
            "Provide both --from and --to, or neither."
        )

    if from_date is not None:
        start = _iso_date(
            from_date,
            "from_date",
        )
        end = _iso_date(
            to_date,
            "to_date",
        )
    else:
        today = (
            now or datetime.now(zone)
        ).astimezone(zone).date()

        start = today - timedelta(
            days=_days(
                env,
                "SYNC_DAYS_BACK",
                7,
            )
        )

        end = today + timedelta(
            days=_days(
                env,
                "SYNC_DAYS_AHEAD",
                90,
            )
        )

    if start > end:
        raise ConfigurationError(
            "from_date must be on or before to_date."
        )

    return (
        start.isoformat(),
        end.isoformat(),
        zone,
    )


def _due_date(value, zone):
    try:
        if re.fullmatch(
            r"\d{4}-\d{2}-\d{2}",
            value,
        ):
            return date.fromisoformat(value)

        parsed = datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )

        if parsed.tzinfo:
            return parsed.astimezone(zone).date()

        return parsed.date()

    except (
        ValueError,
        TypeError,
        OverflowError,
    ):
        raise CalendarError(
            "A homework due date could not be interpreted; "
            "no sync was started."
        ) from None


def _source(student_id):
    if type(student_id) is not int or student_id <= 0:
        raise CalendarError(
            "An authenticated student identity is required "
            "for calendar sync."
        )

    return hashlib.sha256(
        (
            "classcharts-student-v1:"
            + str(student_id)
        ).encode()
    ).hexdigest()


def _is_done(item):
    return (
        item.ticked is True
        or item.status == "completed"
    )


def _legacy_event_id(source, homework_id):
    return (
        "cc"
        + hashlib.sha256(
            (
                source
                + ":"
                + str(homework_id)
            ).encode()
        ).hexdigest()
    )


def _grouped_event_id(source, due):
    return (
        "cc"
        + hashlib.sha256(
            (
                source
                + ":due:"
                + due.isoformat()
            ).encode()
        ).hexdigest()
    )


def legacy_event_ids(
    homework: list[Homework],
    student_id: int,
):
    """
    Return IDs used by the old one-event-per-homework format.

    These are used only to migrate events previously created by this
    automation.
    """

    source = _source(student_id)

    return sorted(
        {
            _legacy_event_id(
                source,
                item.id,
            )
            for item in homework
            if item.due_date
        }
    )


def build_events(
    homework: list[Homework],
    student_id: int,
    zone,
) -> list[dict]:
    """
    Build one Google Calendar event per due date.

    Multiple homework items due on the same day are listed inside the
    same event.
    """

    source = _source(student_id)

    grouped = {}

    for item in homework:
        if not item.due_date:
            continue

        due = _due_date(
            item.due_date,
            zone,
        )

        grouped.setdefault(
            due,
            [],
        ).append(item)

    events = []

    for due in sorted(grouped):
        items = sorted(
            grouped[due],
            key=lambda item: (
                (item.subject or "").casefold(),
                item.title.casefold(),
                str(item.id),
            ),
        )

        start = datetime.combine(
            due,
            time(7, 0),
            tzinfo=zone,
        )

        end = start + timedelta(
            minutes=15
        )

        all_done = all(
            _is_done(item)
            for item in items
        )

        lines = []

        for item in items:
            title = " ".join(
                item.title.split()
            )

            subject = " ".join(
                (item.subject or "").split()
            )

            text = (
                ((subject + ": ") if subject else "")
                + title
            )

            if _is_done(item):
                text = "Done: " + text

            lines.append(text)

        count = len(items)

        summary = (
            ("Done: " if all_done else "")
            + "Homework due today"
            + (
                " (" + str(count) + ")"
                if count > 1
                else ""
            )
        )

        description = (
            "Homework due today:\n\n"
            + "\n".join(lines)
            + "\n\n"
            + "Open ClassCharts for homework instructions.\n"
            + "https://www.classcharts.com/student"
        )

        event = {
            "id": _grouped_event_id(
                source,
                due,
            ),
            "summary": summary[:1024],
            "description": description,
            "start": {
                "dateTime": start.isoformat(),
                "timeZone": getattr(
                    zone,
                    "key",
                    "Europe/London",
                ),
            },
            "end": {
                "dateTime": end.isoformat(),
                "timeZone": getattr(
                    zone,
                    "key",
                    "Europe/London",
                ),
            },
            "status": "confirmed",
            "visibility": "private",
            "transparency": "transparent",
            "reminders": (
                {
                    "useDefault": False,
                }
                if all_done
                else {
                    "useDefault": True,
                }
            ),
            "extendedProperties": {
                "private": {
                    "cc_managed": "v2",
                    "cc_source": source,
                    "cc_due": due.isoformat(),
                }
            },
        }

        events.append(event)

    return events


def synchronize(
    student,
    calendar,
    *,
    from_date,
    to_date,
    zone,
    apply=False,
):
    homework = student.get_homework(
        from_date=from_date,
        to_date=to_date,
        display_date="due_date",
    )

    events = build_events(
        homework,
        student.student_id,
        zone,
    )

    old_event_ids = legacy_event_ids(
        homework,
        student.student_id,
    )

    source = _source(
        student.student_id
    )

    calendar.check_access()

    # Create/update the new grouped events first.
    # We do not delete anything until these succeed.
    for event in events:
        calendar.upsert(
            event,
            dry_run=not apply,
        )

    # Remove only events created by the old v1 implementation.
    # Manual calendar events can never match these ownership markers.
    for event_id in old_event_ids:
        calendar.delete_owned(
            event_id,
            {
                "cc_managed": "v1",
                "cc_source": source,
            },
            dry_run=not apply,
        )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Sync homework to Google Calendar; "
            "defaults to a read-only dry run."
        )
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create and update managed calendar events.",
    )

    parser.add_argument(
        "--from",
        dest="from_date",
        metavar="YYYY-MM-DD",
    )

    parser.add_argument(
        "--to",
        dest="to_date",
        metavar="YYYY-MM-DD",
    )

    args = parser.parse_args(argv)

    try:
        start, end, zone = date_window(
            os.environ,
            from_date=args.from_date,
            to_date=args.to_date,
        )

        with StudentClient.from_env() as student, GoogleCalendar.from_env() as calendar:
            synchronize(
                student,
                calendar,
                from_date=start,
                to_date=end,
                zone=zone,
                apply=args.apply,
            )

    except ClassChartsError as exc:
        print(
            str(exc),
            file=sys.stderr,
        )
        return 1

    except Exception:
        print(
            "Sync failed unexpectedly. "
            "No diagnostic payload was logged.",
            file=sys.stderr,
        )
        return 1

    print(
        "Calendar sync succeeded."
        if args.apply
        else (
            "Calendar dry run succeeded. "
            "No events were changed."
        )
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())