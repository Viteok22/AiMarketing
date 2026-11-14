from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.time_parse import PublishTimeError, parse_custom_publish_time


NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone(timedelta(hours=5)))


class CustomPublishTimeTests(unittest.TestCase):
    def test_day_month_and_time(self) -> None:
        result = parse_custom_publish_time("15.11 18:30", now=NOW)
        self.assertEqual(result, datetime(2026, 11, 15, 18, 30, tzinfo=NOW.tzinfo))

    def test_full_date_with_dots(self) -> None:
        result = parse_custom_publish_time("15.11.2027 09:05", now=NOW)
        self.assertEqual(result, datetime(2027, 11, 15, 9, 5, tzinfo=NOW.tzinfo))

    def test_two_digit_year(self) -> None:
        result = parse_custom_publish_time("15.11.27 09:05", now=NOW)
        self.assertEqual(result, datetime(2027, 11, 15, 9, 5, tzinfo=NOW.tzinfo))

    def test_iso_date(self) -> None:
        result = parse_custom_publish_time("2027-01-02 08:05", now=NOW)
        self.assertEqual(result, datetime(2027, 1, 2, 8, 5, tzinfo=NOW.tzinfo))

    def test_dot_as_time_separator(self) -> None:
        result = parse_custom_publish_time("15.11 18.30", now=NOW)
        self.assertEqual(result, datetime(2026, 11, 15, 18, 30, tzinfo=NOW.tzinfo))

    def test_named_days(self) -> None:
        today = parse_custom_publish_time("сегодня 18:30", now=NOW)
        tomorrow = parse_custom_publish_time("завтра 10:00", now=NOW)
        after = parse_custom_publish_time("послезавтра 09:00", now=NOW)
        self.assertEqual(today, datetime(2026, 11, 14, 18, 30, tzinfo=NOW.tzinfo))
        self.assertEqual(tomorrow, datetime(2026, 11, 15, 10, 0, tzinfo=NOW.tzinfo))
        self.assertEqual(after, datetime(2026, 11, 16, 9, 0, tzinfo=NOW.tzinfo))

    def test_relative_hours_and_minutes(self) -> None:
        self.assertEqual(
            parse_custom_publish_time("через 2 часа", now=NOW),
            NOW + timedelta(hours=2),
        )
        self.assertEqual(
            parse_custom_publish_time("через 1 час 30 минут", now=NOW),
            NOW + timedelta(hours=1, minutes=30),
        )
        self.assertEqual(
            parse_custom_publish_time("через 45 минут", now=NOW),
            NOW + timedelta(minutes=45),
        )

    def test_relative_day(self) -> None:
        self.assertEqual(
            parse_custom_publish_time("через 1 день", now=NOW),
            NOW + timedelta(days=1),
        )

    def test_past_time_is_rejected(self) -> None:
        with self.assertRaises(PublishTimeError):
            parse_custom_publish_time("14.11 11:59", now=NOW)

    def test_current_time_is_rejected(self) -> None:
        with self.assertRaises(PublishTimeError):
            parse_custom_publish_time("14.11 12:00", now=NOW)

    def test_invalid_values_are_rejected(self) -> None:
        for value in ("32.11 18:30", "15.11 25:00", "15.11", "просто текст"):
            with self.subTest(value=value):
                with self.assertRaises(PublishTimeError):
                    parse_custom_publish_time(value, now=NOW)


if __name__ == "__main__":
    unittest.main()
