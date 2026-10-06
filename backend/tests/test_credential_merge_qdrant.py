"""Regression test for credential update merging on the standalone qdrant credential.

merge_credential_config_for_update had no branch for the standalone `qdrant`
CredentialType (distinct from the RAG credential's own `db_type == "qdrant"`
case, which already merges correctly), so editing the credential from the UI -
where neither qdrant_api_key nor openai_api_key is prefilled, since the dialog
never shows a stored secret back - silently wiped whichever key wasn't
retyped instead of keeping it.
"""

import unittest

from app.api.credentials import get_public_credential_fields, merge_credential_config_for_update
from app.db.models import CredentialType


class TestQdrantCredentialMerge(unittest.TestCase):
    def _full_config(self) -> dict:
        return {
            "qdrant_host": "https://qdrant.example.com",
            "qdrant_port": "6333",
            "qdrant_api_key": "original_qdrant_key",
            "openai_api_key": "original_openai_key",
        }

    def test_editing_host_keeps_both_secrets(self) -> None:
        # What the UI actually sends on edit: neither secret field is prefilled,
        # so both come back blank unless the user retypes them.
        merged = merge_credential_config_for_update(
            CredentialType.qdrant,
            self._full_config(),
            {
                "qdrant_host": "https://new-host.example.com",
                "qdrant_port": "6333",
                "qdrant_api_key": "",
                "openai_api_key": "",
            },
        )

        self.assertEqual(merged["qdrant_host"], "https://new-host.example.com")
        self.assertEqual(merged["qdrant_api_key"], "original_qdrant_key")
        self.assertEqual(merged["openai_api_key"], "original_openai_key")

    def test_rotating_openai_key_keeps_qdrant_api_key(self) -> None:
        merged = merge_credential_config_for_update(
            CredentialType.qdrant,
            self._full_config(),
            {
                "qdrant_host": "https://qdrant.example.com",
                "qdrant_port": "6333",
                "qdrant_api_key": "",
                "openai_api_key": "new_openai_key",
            },
        )

        self.assertEqual(merged["openai_api_key"], "new_openai_key")
        self.assertEqual(merged["qdrant_api_key"], "original_qdrant_key")

    def test_rotating_qdrant_api_key_overwrites(self) -> None:
        merged = merge_credential_config_for_update(
            CredentialType.qdrant,
            self._full_config(),
            {
                "qdrant_host": "https://qdrant.example.com",
                "qdrant_port": "6333",
                "qdrant_api_key": "new_qdrant_key",
                "openai_api_key": "original_openai_key",
            },
        )

        self.assertEqual(merged["qdrant_api_key"], "new_qdrant_key")

    def test_editing_port_updates_it(self) -> None:
        merged = merge_credential_config_for_update(
            CredentialType.qdrant,
            self._full_config(),
            {
                "qdrant_host": "https://qdrant.example.com",
                "qdrant_port": "6334",
                "qdrant_api_key": "",
                "openai_api_key": "",
            },
        )

        self.assertEqual(merged["qdrant_port"], "6334")
        self.assertEqual(merged["openai_api_key"], "original_openai_key")


class TestQdrantCredentialPublicFields(unittest.TestCase):
    def test_public_fields_returns_host_and_port_only(self) -> None:
        fields = get_public_credential_fields(
            CredentialType.qdrant,
            {
                "qdrant_host": "https://qdrant.example.com",
                "qdrant_port": "6333",
                "qdrant_api_key": "original_qdrant_key",
                "openai_api_key": "original_openai_key",
            },
        )

        self.assertEqual(
            fields,
            {"qdrant_host": "https://qdrant.example.com", "qdrant_port": "6333"},
        )
        self.assertNotIn("qdrant_api_key", fields)
        self.assertNotIn("openai_api_key", fields)

    def test_public_fields_defaults_port_when_missing(self) -> None:
        fields = get_public_credential_fields(
            CredentialType.qdrant,
            {"qdrant_host": "https://qdrant.example.com"},
        )

        self.assertEqual(fields["qdrant_port"], "6333")


if __name__ == "__main__":
    unittest.main()
