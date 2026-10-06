"""Regression test for credential update merging on the standalone qdrant credential.

merge_credential_config_for_update had no branch for the standalone `qdrant`
CredentialType (distinct from the RAG credential's own `db_type == "qdrant"`
case, which already merges correctly), so editing the credential from the UI -
where qdrant_api_key is left blank unless retyped, since it's optional for a
self-hosted Qdrant instance with no auth - silently wiped the stored key
instead of keeping it.
"""

import unittest

from app.api.credentials import merge_credential_config_for_update
from app.db.models import CredentialType


class TestQdrantCredentialMerge(unittest.TestCase):
    def _full_config(self) -> dict:
        return {
            "qdrant_host": "https://qdrant.example.com",
            "qdrant_port": "6333",
            "qdrant_api_key": "original_qdrant_key",
            "openai_api_key": "original_openai_key",
        }

    def test_editing_host_keeps_qdrant_api_key(self) -> None:
        merged = merge_credential_config_for_update(
            CredentialType.qdrant,
            self._full_config(),
            {
                "qdrant_host": "https://new-host.example.com",
                "qdrant_port": "6333",
                "qdrant_api_key": "",
                "openai_api_key": "original_openai_key",
            },
        )

        self.assertEqual(merged["qdrant_host"], "https://new-host.example.com")
        self.assertEqual(merged["qdrant_api_key"], "original_qdrant_key")

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
                "openai_api_key": "original_openai_key",
            },
        )

        self.assertEqual(merged["qdrant_port"], "6334")


if __name__ == "__main__":
    unittest.main()
