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


def date_window(
    env,
    *,
    from_date=None,
    to_date=None,
    now=None,
):
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
            return parsed.astimezone(
                zone
            ).date()

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


def build_events(
    homework: list[Homework],
    student_id: int,
    zone,
) -> list[dict]:
    """Validate the entire batch before writing."""

    if (
        type(student_id) is not int
        or student_id <= 0
    ):
        raise CalendarError(
            "An authenticated student identity is required "
            "for calendar sync."
        )

    source = hashlib.sha256(
        (
            "classcharts-student-v1:"
            + str(student_id)
        ).encode()
    ).hexdigest()

    events = {}

    for item in homework:
        if not item.due_date:
            continue

        due = _due_date(
            item.due_date,
            zone,
        )

        start = datetime.combine(
            due,
            time(7, 0),
            tzinfo=zone,
        )

        end = start + timedelta(
            minutes=15
        )

        event_id = (
            "cc"
            + hashlib.sha256(
                (
                    source
                    + ":"
                    + str(item.id)
                ).encode()
            ).hexdigest()
        )

        done = (
            item.ticked is True
            or item.status == "completed"
        )

        title = " ".join(
            item.title.split()
        )

        subject = " ".join(
            (item.subject or "").split()
        )

        summary = (
            ("Done: " if done else "")
            + (
                (subject + ": ")
                if subject
                else ""
            )
            + title
        )

        event = {
            "id": event_id,
            "summary": summary[:1024],
            "description": (
                "Open ClassCharts for homework instructions.\n"
                "https://www.classcharts.com/student"
            ),
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

            # Pending homework uses Kirill's default
            # notifications configured on the Homework calendar.
            #
            # Completed homework explicitly disables notifications.
            "reminders": (
                {
                    "useDefault": False,
                }
                if done
                else {
                    "useDefault": True,
                }
            ),

            "extendedProperties": {
                "private": {
                    "cc_managed": "v1",
                    "cc_source": source,
                }
            },
        }

        if (
            event_id in events
            and events[event_id] != event
        ):
            raise CalendarError(
                "Conflicting homework records were returned; "
                "no sync was started."
            )

        events[event_id] = event

    return list(
        events.values()
    )


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

    calendar.check_access()

    for event in events:
        calendar.upsert(
            event,
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
        help=(
            "Create and update managed calendar events."
        ),
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