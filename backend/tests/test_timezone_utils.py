import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.config import settings
from app.services.timezone_utils import get_configured_timezone, normalize_datetime_to_timezone


class TestGetConfiguredTimezone(unittest.TestCase):
    # effective_timezone is a read-only @property derived from the real `timezone`
    # field, so the field itself is what has to be patched, not the property.
    def test_returns_the_configured_zone_when_valid(self) -> None:
        with patch.object(settings, "timezone", "America/New_York"):
            tz = get_configured_timezone()
        self.assertEqual(tz, ZoneInfo("America/New_York"))

    def test_falls_back_to_utc_when_the_configured_zone_is_invalid(self) -> None:
        with patch.object(settings, "timezone", "Not/A_Real_Zone"):
            tz = get_configured_timezone()
        self.assertEqual(tz, timezone.utc)


class TestNormalizeDatetimeToTimezone(unittest.TestCase):
    def test_attaches_the_configured_timezone_to_a_naive_datetime(self) -> None:
        naive = datetime(2026, 1, 1, 12, 0, 0)
        target = ZoneInfo("America/New_York")

        result = normalize_datetime_to_timezone(naive, target)

        # A naive datetime has no timezone at all, so this is attaching one, not
        # converting: the wall-clock time must stay 12:00, just now labeled as
        # being in America/New_York.
        self.assertEqual(result.tzinfo, target)
        self.assertEqual(result.hour, 12)
        self.assertEqual(result.minute, 0)

    def test_converts_an_aware_datetime_into_the_configured_timezone(self) -> None:
        aware = datetime(2026, 1, 1, 17, 0, 0, tzinfo=timezone.utc)
        target = ZoneInfo("America/New_York")

        result = normalize_datetime_to_timezone(aware, target)

        # An aware datetime already knows its own instant in time, so this is a
        # real conversion: 17:00 UTC in January is 12:00 in America/New_York
        # (UTC-5, standard time), the same instant, different wall-clock time.
        self.assertEqual(result.tzinfo, target)
        self.assertEqual(result.hour, 12)
        self.assertEqual(result, aware)


if __name__ == "__main__":
    unittest.main()
