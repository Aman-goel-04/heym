"""Tests for the telegram credential update merge.

Regression tests for a bug where a partial update (e.g. rotating just the bot
token, which is what the frontend sends since telegramSecretToken is reset to
"" on every edit-open) silently wiped the webhook secret_token, because
merge_credential_config_for_update had no branch for telegram and fell through
to a full overwrite.
"""

import unittest

from app.api.credentials import merge_credential_config_for_update
from app.db.models import CredentialType


class TestTelegramCredentialMerge(unittest.TestCase):
    def _full_config(self) -> dict:
        return {"bot_token": "111:AAAoriginal", "secret_token": "original-secret"}

    def test_partial_update_keeps_secret_token(self) -> None:
        merged = merge_credential_config_for_update(
            CredentialType.telegram,
            self._full_config(),
            {"bot_token": "222:ANewToken"},
        )
        self.assertEqual(merged["bot_token"], "222:ANewToken")
        self.assertEqual(merged["secret_token"], "original-secret")

    def test_blank_secret_token_keeps_the_stored_one(self) -> None:
        merged = merge_credential_config_for_update(
            CredentialType.telegram,
            self._full_config(),
            {"bot_token": "222:ANewToken", "secret_token": ""},
        )
        self.assertEqual(merged["secret_token"], "original-secret")

    def test_non_blank_secret_token_overwrites_the_stored_one(self) -> None:
        merged = merge_credential_config_for_update(
            CredentialType.telegram,
            self._full_config(),
            {"secret_token": "new-secret"},
        )
        self.assertEqual(merged["secret_token"], "new-secret")

    def test_updating_secret_token_alone_does_not_crash_or_need_bot_token(self) -> None:
        """Regression for the original typo: incoming_secret_token was computed inside
        `if incoming_bot_token:`, so sending secret_token without bot_token raised
        UnboundLocalError instead of merging."""
        merged = merge_credential_config_for_update(
            CredentialType.telegram,
            self._full_config(),
            {"secret_token": "rotated-secret-only"},
        )
        self.assertEqual(merged["bot_token"], "111:AAAoriginal")
        self.assertEqual(merged["secret_token"], "rotated-secret-only")


if __name__ == "__main__":
    unittest.main()
