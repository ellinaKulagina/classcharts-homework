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


class ReminderTests(unittest.TestCase):
    def event_for(self, item):
        events = build_events([item], 999, ZONE)
        self.assertEqual(len(events), 1)
        return events[0]

    def test_event_is_at_7am_on_due_date(self):
        event = self.event_for(homework(due="2026-09-21"))

        self.assertEqual(
            event["start"]["dateTime"],
            "2026-09-21T07:00:00+01:00",
        )

        self.assertEqual(
            event["end"]["dateTime"],
            "2026-09-21T07:15:00+01:00",
        )

    def test_monday_due_reminders(self):
        event = self.event_for(homework(due="2026-09-21"))

        self.assertEqual(
            event["reminders"]["overrides"],
            [
                {"method": "popup", "minutes": 2820},
                {"method": "popup", "minutes": 840},
                {"method": "popup", "minutes": 0},
            ],
        )

    def test_sunday_due_uses_previous_saturday(self):
        event = self.event_for(homework(due="2026-09-27"))

        self.assertEqual(
            event["reminders"]["overrides"],
            [
                {"method": "popup", "minutes": 11460},
                {"method": "popup", "minutes": 840},
                {"method": "popup", "minutes": 0},
            ],
        )

    def test_dst_change_keeps_local_times(self):
        event = self.event_for(homework(due="2026-10-26"))

        self.assertEqual(
            event["start"]["dateTime"],
            "2026-10-26T07:00:00+00:00",
        )

        self.assertEqual(
            event["reminders"]["overrides"],
            [
                {"method": "popup", "minutes": 2880},
                {"method": "popup", "minutes": 840},
                {"method": "popup", "minutes": 0},
            ],
        )

    def test_completed_homework_has_no_reminders(self):
        event = self.event_for(
            homework(
                due="2026-09-21",
                ticked=True,
                status="completed",
            )
        )

        self.assertEqual(
            event["reminders"],
            {"useDefault": False},
        )

        self.assertTrue(
            event["summary"].startswith("Done: ")
        )


if __name__ == "__main__":
    unittest.main()
