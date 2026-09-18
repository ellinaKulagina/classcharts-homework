import unittest
from zoneinfo import ZoneInfo

from classcharts_homework.client import Homework
from classcharts_homework.sync import build_events


ZONE = ZoneInfo("Europe/London")


def homework(*, due, ticked=False, status="not_completed"):
    return Homework(
        id=123,
        title="Synthetic homework",
        description=None,
        subject="Maths",
        due_date=due,
        issue_date="2026-09-01",
        status=status,
        ticked=ticked,
    )


class SyncEventTests(unittest.TestCase):
    def event_for(self, item):
        events = build_events(
            [item],
            999,
            ZONE,
        )

        self.assertEqual(
            len(events),
            1,
        )

        return events[0]

    def test_event_is_at_7am_on_due_date(self):
        event = self.event_for(
            homework(
                due="2026-09-21",
            )
        )

        self.assertEqual(
            event["start"]["dateTime"],
            "2026-09-21T07:00:00+01:00",
        )

        self.assertEqual(
            event["end"]["dateTime"],
            "2026-09-21T07:15:00+01:00",
        )

    def test_pending_homework_uses_calendar_defaults(self):
        event = self.event_for(
            homework(
                due="2026-09-21",
            )
        )

        self.assertEqual(
            event["reminders"],
            {
                "useDefault": True,
            },
        )

    def test_completed_homework_disables_reminders(self):
        event = self.event_for(
            homework(
                due="2026-09-21",
                ticked=True,
                status="completed",
            )
        )

        self.assertEqual(
            event["reminders"],
            {
                "useDefault": False,
            },
        )

    def test_completed_homework_is_marked_done(self):
        event = self.event_for(
            homework(
                due="2026-09-21",
                ticked=True,
                status="completed",
            )
        )

        self.assertTrue(
            event["summary"].startswith(
                "Done: "
            )
        )

    def test_status_completed_is_enough_to_mark_done(self):
        event = self.event_for(
            homework(
                due="2026-09-21",
                ticked=False,
                status="completed",
            )
        )

        self.assertEqual(
            event["reminders"],
            {
                "useDefault": False,
            },
        )

        self.assertTrue(
            event["summary"].startswith(
                "Done: "
            )
        )

    def test_ticked_homework_is_enough_to_mark_done(self):
        event = self.event_for(
            homework(
                due="2026-09-21",
                ticked=True,
                status="not_completed",
            )
        )

        self.assertEqual(
            event["reminders"],
            {
                "useDefault": False,
            },
        )

        self.assertTrue(
            event["summary"].startswith(
                "Done: "
            )
        )

    def test_pending_homework_does_not_get_done_prefix(self):
        event = self.event_for(
            homework(
                due="2026-09-21",
            )
        )

        self.assertFalse(
            event["summary"].startswith(
                "Done: "
            )
        )

    def test_dst_date_keeps_7am_local_time(self):
        event = self.event_for(
            homework(
                due="2026-10-26",
            )
        )

        self.assertEqual(
            event["start"]["dateTime"],
            "2026-10-26T07:00:00+00:00",
        )

        self.assertEqual(
            event["end"]["dateTime"],
            "2026-10-26T07:15:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()