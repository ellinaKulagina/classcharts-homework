import unittest
from zoneinfo import ZoneInfo

from classcharts_homework.client import Homework
from classcharts_homework.sync import (
    build_events,
    legacy_event_ids,
    synchronize,
)


ZONE = ZoneInfo("Europe/London")


def homework(
    *,
    item_id=123,
    due="2026-09-21",
    title="Synthetic homework",
    subject="Maths",
    ticked=False,
    status="not_completed",
):
    return Homework(
        id=item_id,
        title=title,
        description=None,
        subject=subject,
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

    def test_event_is_at_7am(self):
        event = self.event_for(
            homework()
        )

        self.assertEqual(
            event["start"]["dateTime"],
            "2026-09-21T07:00:00+01:00",
        )

        self.assertEqual(
            event["end"]["dateTime"],
            "2026-09-21T07:15:00+01:00",
        )

    def test_three_tasks_same_day_become_one_event(self):
        events = build_events(
            [
                homework(
                    item_id=1,
                    title="Algebra worksheet",
                    subject="Maths",
                ),
                homework(
                    item_id=2,
                    title="Cells questions",
                    subject="Science",
                ),
                homework(
                    item_id=3,
                    title="Read chapter 4",
                    subject="English",
                ),
            ],
            999,
            ZONE,
        )

        self.assertEqual(
            len(events),
            1,
        )

        event = events[0]

        self.assertEqual(
            event["summary"],
            "Homework due today (3)",
        )

        self.assertIn(
            "Maths: Algebra worksheet",
            event["description"],
        )

        self.assertIn(
            "Science: Cells questions",
            event["description"],
        )

        self.assertIn(
            "English: Read chapter 4",
            event["description"],
        )

    def test_different_days_create_different_events(self):
        events = build_events(
            [
                homework(
                    item_id=1,
                    due="2026-09-21",
                ),
                homework(
                    item_id=2,
                    due="2026-09-22",
                ),
            ],
            999,
            ZONE,
        )

        self.assertEqual(
            len(events),
            2,
        )

        self.assertNotEqual(
            events[0]["id"],
            events[1]["id"],
        )

    def test_mixed_day_keeps_notifications(self):
        events = build_events(
            [
                homework(
                    item_id=1,
                    ticked=True,
                    status="completed",
                ),
                homework(
                    item_id=2,
                    title="Still unfinished",
                ),
            ],
            999,
            ZONE,
        )

        event = events[0]

        self.assertEqual(
            event["reminders"],
            {
                "useDefault": True,
            },
        )

        self.assertFalse(
            event["summary"].startswith(
                "Done: "
            )
        )

        self.assertIn(
            "Done: Maths: Synthetic homework",
            event["description"],
        )

        self.assertIn(
            "Maths: Still unfinished",
            event["description"],
        )

    def test_all_done_disables_notifications(self):
        events = build_events(
            [
                homework(
                    item_id=1,
                    ticked=True,
                    status="completed",
                ),
                homework(
                    item_id=2,
                    ticked=True,
                    status="completed",
                ),
            ],
            999,
            ZONE,
        )

        event = events[0]

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

    def test_grouped_event_id_is_independent_of_task_order(self):
        first = homework(
            item_id=1,
            title="First",
        )

        second = homework(
            item_id=2,
            title="Second",
        )

        event_a = build_events(
            [first, second],
            999,
            ZONE,
        )[0]

        event_b = build_events(
            [second, first],
            999,
            ZONE,
        )[0]

        self.assertEqual(
            event_a["id"],
            event_b["id"],
        )

        self.assertEqual(
            event_a,
            event_b,
        )

    def test_dst_keeps_7am_local_time(self):
        event = self.event_for(
            homework(
                due="2026-10-26",
            )
        )

        self.assertEqual(
            event["start"]["dateTime"],
            "2026-10-26T07:00:00+00:00",
        )

    def test_legacy_ids_are_per_homework_item(self):
        items = [
            homework(
                item_id=1,
            ),
            homework(
                item_id=2,
            ),
            homework(
                item_id=3,
            ),
        ]

        ids = legacy_event_ids(
            items,
            999,
        )

        self.assertEqual(
            len(ids),
            3,
        )

        self.assertEqual(
            len(set(ids)),
            3,
        )


class FakeStudent:
    def __init__(self, items):
        self.student_id = 999
        self.items = items

    def get_homework(
        self,
        *,
        from_date,
        to_date,
        display_date,
    ):
        return self.items


class FakeCalendar:
    def __init__(self):
        self.events = []
        self.deleted = []
        self.checked = False

    def check_access(self):
        self.checked = True

    def upsert(
        self,
        event,
        *,
        dry_run,
    ):
        self.events.append(
            (
                event,
                dry_run,
            )
        )

    def delete_owned(
        self,
        event_id,
        expected_private,
        *,
        dry_run,
    ):
        self.deleted.append(
            (
                event_id,
                expected_private,
                dry_run,
            )
        )


class MigrationTests(unittest.TestCase):
    def test_three_old_events_are_migrated_to_one_grouped_event(self):
        items = [
            homework(
                item_id=1,
                title="First",
            ),
            homework(
                item_id=2,
                title="Second",
            ),
            homework(
                item_id=3,
                title="Third",
            ),
        ]

        student = FakeStudent(
            items
        )

        calendar = FakeCalendar()

        synchronize(
            student,
            calendar,
            from_date="2026-09-01",
            to_date="2026-10-01",
            zone=ZONE,
            apply=True,
        )

        self.assertTrue(
            calendar.checked
        )

        self.assertEqual(
            len(calendar.events),
            1,
        )

        self.assertEqual(
            len(calendar.deleted),
            3,
        )

        for _, markers, dry_run in calendar.deleted:
            self.assertEqual(
                markers["cc_managed"],
                "v1",
            )

            self.assertFalse(
                dry_run
            )

    def test_dry_run_does_not_apply_changes(self):
        items = [
            homework(
                item_id=1,
            ),
            homework(
                item_id=2,
            ),
        ]

        student = FakeStudent(
            items
        )

        calendar = FakeCalendar()

        synchronize(
            student,
            calendar,
            from_date="2026-09-01",
            to_date="2026-10-01",
            zone=ZONE,
            apply=False,
        )

        self.assertEqual(
            calendar.events[0][1],
            True,
        )

        for _, _, dry_run in calendar.deleted:
            self.assertTrue(
                dry_run
            )


if __name__ == "__main__":
    unittest.main()