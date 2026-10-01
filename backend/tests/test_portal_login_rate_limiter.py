import unittest

from app.services.portal_rate_limiter import PortalLoginRateLimiter


class PortalLoginRateLimiterTests(unittest.TestCase):
    def test_successful_logins_never_ban(self) -> None:
        limiter = PortalLoginRateLimiter()
        wf, ip = "wf-1", "1.2.3.4"
        for _ in range(10):
            is_banned, _ = limiter.is_banned(wf, ip)
            self.assertFalse(is_banned)
            limiter.record_successful_login(wf, ip)

    def test_three_failed_attempts_still_bans_the_fourth(self) -> None:
        limiter = PortalLoginRateLimiter()
        wf, ip = "wf-2", "5.6.7.8"
        for _ in range(3):
            is_banned, _ = limiter.is_banned(wf, ip)
            self.assertFalse(is_banned)
            limiter.record_failed_attempt(wf, ip)
        is_banned, retry_after = limiter.is_banned(wf, ip)
        self.assertTrue(is_banned)
        self.assertGreater(retry_after, 0)

    def test_a_success_does_not_erase_prior_failures(self) -> None:
        """Guards against a reset-to-0 bypass: someone with a valid login on the same
        workflow+IP must not be able to launder away failures."""
        limiter = PortalLoginRateLimiter()
        wf, ip = "wf-3", "9.9.9.9"
        limiter.is_banned(wf, ip)
        limiter.record_failed_attempt(wf, ip)
        limiter.is_banned(wf, ip)
        limiter.record_failed_attempt(wf, ip)
        # A correct login for a *different* user, same workflow+IP, interleaved here.
        limiter.is_banned(wf, ip)
        limiter.record_successful_login(wf, ip)
        # One more real failure should now be enough to trip the ban (3rd failure).
        limiter.is_banned(wf, ip)
        limiter.record_failed_attempt(wf, ip)
        is_banned, _ = limiter.is_banned(wf, ip)
        self.assertTrue(is_banned)
