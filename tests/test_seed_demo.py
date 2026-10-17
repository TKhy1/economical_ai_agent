"""The demo seeder must never date a transaction in the future.

The fixture offsets describe a Monday-to-Sunday week. Running the seeder on a Monday
would otherwise place six of the ten rows in the future; `planned_occurred_dates`
slides the schedule back just far enough that the newest row lands on today.
"""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seed_demo_data as seed  # noqa: E402


class SeedOccurredDatesTest(unittest.TestCase):
    def test_no_seeded_transaction_is_in_the_future(self):
        monday = date(2026, 10, 12)
        offsets = [row[4] for row in seed.DEMO_TRANSACTIONS]
        for elapsed in range(7):
            today = monday + timedelta(days=elapsed)
            with self.subTest(today=today):
                dates = seed.planned_occurred_dates(today, monday, offsets)
                self.assertEqual(len(dates), len(offsets))
                self.assertTrue(all(day <= today for day in dates))
                self.assertEqual(max(dates), today)

    def test_full_week_keeps_offsets_when_run_on_sunday(self):
        monday = date(2026, 10, 12)
        dates = seed.planned_occurred_dates(
            monday + timedelta(days=6), monday, [0, 1, 6]
        )
        self.assertEqual(
            dates,
            [monday, monday + timedelta(days=1), monday + timedelta(days=6)],
        )

    def test_midweek_only_clamps_the_future_rows(self):
        monday = date(2026, 10, 12)
        saturday = monday + timedelta(days=5)
        self.assertEqual(
            seed.planned_occurred_dates(saturday, monday, [0, 6]),
            [monday, saturday],
        )

    def test_empty_offsets(self):
        today = date(2026, 10, 17)
        self.assertEqual(seed.planned_occurred_dates(today, today, []), [])


if __name__ == "__main__":
    unittest.main()
