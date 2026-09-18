"""One-way ClassCharts -> Google Calendar synchronization. Dry-run by default."""

import argparse
import hashlib
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .client import ClassChartsError, ConfigurationError, Homework, StudentClient, _iso_date
from .google_calendar import CalendarError, GoogleCalendar


def _zone(value):
    try:
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ConfigurationError("CALENDAR_TIMEZONE must be a valid IANA timezone.") from None


def _days(env, name, default):
    value = env.get(name, str(default))
    if not re.fullmatch(r"[0-9]{1,3}", value) or not 0 <= int(value) <= 365:
        raise ConfigurationError(name + " must be an integer from 0 to 365.")
    return int(value)


def date_window(env, *, from_date=None, to_date=None, now=None):
    zone = _zone(env.get("CALENDAR_TIMEZONE", "Europe/London"))
    if (from_date is None) != (to_date is None):
        raise ConfigurationError("Provide both --from and --to, or neither.")
    if from_date is not None:
        start, end = _iso_date(from_date, "from_date"), _iso_date(to_date, "to_date")
    else:
        today = (now or datetime.now(zone)).astimezone(zone).date()
        start = today - timedelta(days=_days(env, "SYNC_DAYS_BACK", 7))
        end = today + timedelta(days=_days(env, "SYNC_DAYS_AHEAD", 90))
    if start > end:
        raise ConfigurationError("from_date must be on or before to_date.")
    return start.isoformat(), end.isoformat(), zone


def _due_date(value, zone):
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(zone).date() if parsed.tzinfo else parsed.date()
    except (ValueError, TypeError, OverflowError):
        raise CalendarError("A homework due date could not be interpreted; no sync was started.") from None


def build_events(homework: list[Homework], student_id: int, zone) -> list[dict]:
    """Validate the entire batch before writing. No descriptions or attachments."""
    if type(student_id) is not int or student_id <= 0:
        raise CalendarError("An authenticated student identity is required for calendar sync.")
    source = hashlib.sha256(("classcharts-student-v1:" + str(student_id)).encode()).hexdigest()
    events = {}
    for item in homework:
        if item.due_date is None or item.due_date == "":
            continue
        due = _due_date(item.due_date, zone)
        try:
            end = due + timedelta(days=1)
        except OverflowError:
            raise CalendarError("A homework due date is outside the supported range.") from None
        # IDs include the pupil to distinguish a shared assignment for siblings.
        # Dates and credentials are excluded so edits/credential rotation are safe.
        event_id = "cc" + hashlib.sha256((source + ":" + str(item.id)).encode()).hexdigest()
        done = item.ticked is True or item.status == "completed"
        title = " ".join(item.title.split())
        subject = " ".join((item.subject or "").split())
        summary = ("Done: " if done else "") + ((subject + ": ") if subject else "") + title
        event = {
            "id": event_id,
            "summary": summary[:1024],
            "description": "Open ClassCharts for homework instructions.\nhttps://www.classcharts.com/student",
            "start": {"date": due.isoformat()},
            "end": {"date": end.isoformat()},
            "status": "confirmed",
            "visibility": "private",
            "transparency": "transparent",
            "reminders": {"useDefault": False},
            "extendedProperties": {"private": {"cc_managed": "v1", "cc_source": source}},
        }
        if event_id in events and events[event_id] != event:
            raise CalendarError("Conflicting homework records were returned; no sync was started.")
        events[event_id] = event
    return list(events.values())


def synchronize(student, calendar, *, from_date, to_date, zone, apply=False):
    homework = student.get_homework(from_date=from_date, to_date=to_date, display_date="due_date")
    events = build_events(homework, student.student_id, zone)
    calendar.check_access()
    for event in events:
        calendar.upsert(event, dry_run=not apply)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sync homework to Google Calendar; defaults to a read-only dry run.")
    parser.add_argument("--apply", action="store_true", help="Create and update managed calendar events.")
    parser.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD")
    args = parser.parse_args(argv)
    try:
        start, end, zone = date_window(os.environ, from_date=args.from_date, to_date=args.to_date)
        with StudentClient.from_env() as student, GoogleCalendar.from_env() as calendar:
            synchronize(student, calendar, from_date=start, to_date=end, zone=zone, apply=args.apply)
    except ClassChartsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # Unexpected dependency failures must not dump tokens or homework in CI.
        print("Sync failed unexpectedly. No diagnostic payload was logged.", file=sys.stderr)
        return 1
    print("Calendar sync succeeded." if args.apply else "Calendar dry run succeeded. No events were changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
