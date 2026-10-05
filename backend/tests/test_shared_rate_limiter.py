"""Unit tests for SharedRateLimiter (sliding-window + ban, in-process)."""

import unittest
from unittest.mock import patch

from app.services.shared_rate_limiter import SharedRateLimiter


def _limiter(
    max_attempts: int = 3, window_seconds: int = 60, ban_seconds: int = 900
) -> SharedRateLimiter:
    # Fresh instance per test - never import the auth_rate_limiter.py singletons
    # (login_limiter/register_limiter), since their in-memory state would leak
    # across tests. Small, easy-to-reason-about numbers by default.
    return SharedRateLimiter(
        namespace="test",
        max_attempts=max_attempts,
        window_seconds=window_seconds,
        ban_seconds=ban_seconds,
    )


class IsAllowedTests(unittest.TestCase):
    def test_first_call_is_allowed(self) -> None:
        limiter = _limiter()
        allowed, retry_after = limiter.is_allowed("1.2.3.4")
        self.assertTrue(allowed)
        self.assertEqual(retry_after, 0)

    def test_calls_under_max_attempts_are_allowed(self) -> None:
        limiter = _limiter()
        results = [limiter.is_allowed("1.2.3.4") for _ in range(3)]

        for allowed, retry_after in results:
            self.assertTrue(allowed)
            self.assertEqual(retry_after, 0)

    def test_exceeding_max_attempts_denies_and_bans(self) -> None:
        limiter = _limiter()
        results = [limiter.is_allowed("1.2.3.4") for _ in range(4)]
        allowed, retry_seconds = results[-1]
        self.assertFalse(allowed)
        self.assertEqual(retry_seconds, 900)

    def test_call_while_banned_counts_down(self) -> None:
        limiter = _limiter()
        with patch("app.services.shared_rate_limiter.time.time") as mock_time:
            mock_time.return_value = 1000.0
            for _ in range(4):
                limiter.is_allowed("1.2.3.4")  # 4th call trips the ban at now=1000

            mock_time.return_value = 1005.0
            allowed, retry_after = limiter.is_allowed("1.2.3.4")
            self.assertFalse(allowed)
            self.assertEqual(retry_after, 895)

    def test_call_after_ban_expires_is_allowed_and_resets(self) -> None:
        limiter = _limiter()
        with patch("app.services.shared_rate_limiter.time.time") as mock_time:
            mock_time.return_value = 1000.0
            for _ in range(4):
                limiter.is_allowed("1.2.3.4")  # 4th call trips the ban at now=1000

            mock_time.return_value = 1900.0
            allowed, retry_after = limiter.is_allowed("1.2.3.4")  # 5th call should be allowed
            self.assertTrue(allowed)
            self.assertEqual(retry_after, 0)

    def test_window_resets_without_hitting_the_ban(self) -> None:
        limiter = _limiter()
        with patch("app.services.shared_rate_limiter.time.time") as mock_time:
            mock_time.return_value = 1000.0
            for _ in range(2):
                allowed, _ = limiter.is_allowed("1.2.3.4")
                self.assertTrue(allowed)

            # Past the 60-second window, without ever tripping the ban. If the
            # window hadn't reset, count would now be 3 then 4 (over
            # max_attempts=3) and the second call below would be denied.
            mock_time.return_value = 1061.0
            for _ in range(2):
                allowed, _ = limiter.is_allowed("1.2.3.4")
                self.assertTrue(allowed)

    def test_different_identifiers_are_isolated(self) -> None:
        limiter = _limiter()
        for _ in range(4):
            allowed, _ = limiter.is_allowed("1.2.3.4")
        self.assertFalse(allowed)

        allowed_b, retry_after_b = limiter.is_allowed("5.6.7.8")
        self.assertTrue(allowed_b)
        self.assertEqual(retry_after_b, 0)


class RecordSuccessTests(unittest.TestCase):
    def test_decrements_count_by_one(self) -> None:
        limiter = _limiter(max_attempts=3)
        limiter.is_allowed("1.2.3.4")  # count -> 1
        limiter.record_success("1.2.3.4")  # count -> 0

        # If count weren't back to 0, the 3rd of these would be the 4th
        # attempt overall and would trip the ban (max_attempts=3).
        results = [limiter.is_allowed("1.2.3.4") for _ in range(3)]
        for allowed, _ in results:
            self.assertTrue(allowed)

    def test_does_not_go_negative(self) -> None:
        limiter = _limiter(max_attempts=3)
        limiter.is_allowed("1.2.3.4")  # count -> 1
        limiter.record_success("1.2.3.4")  # count -> 0
        limiter.record_success("1.2.3.4")  # guarded: count stays 0, not -1

        # If count had gone to -1, it would take 5 new calls to reach a ban
        # (count climbs -1,0,1,2,3,4) instead of the normal 4
        # (count climbs 1,2,3,4) - this pins down exactly which one happened.
        results = [limiter.is_allowed("1.2.3.4") for _ in range(4)]
        allowed, _ = results[-1]
        self.assertFalse(allowed)

    def test_unknown_identifier_is_a_safe_no_op(self) -> None:
        limiter = _limiter()
        # Must not raise, even though is_allowed was never called for this
        # identifier (internal state for it doesn't exist yet).
        limiter.record_success("never-seen")

    def test_record_success_prevents_an_otherwise_earned_ban(self) -> None:
        limiter = _limiter(max_attempts=3)
        for _ in range(3):
            allowed, _ = limiter.is_allowed("1.2.3.4")
            self.assertTrue(allowed)
        # count is now 3 (at the limit) - the very next call would be the 4th
        # and would tip into a ban without a record_success backing it off.

        limiter.record_success("1.2.3.4")  # count -> 2

        allowed, retry_after = limiter.is_allowed("1.2.3.4")  # count -> 3
        self.assertTrue(allowed)
        self.assertEqual(retry_after, 0)


if __name__ == "__main__":
    unittest.main()
