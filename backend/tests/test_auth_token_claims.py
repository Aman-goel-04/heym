"""Tokens can name the client they were issued to, and readers expose it."""

import unittest
import uuid

from app.services.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    read_access_claims,
    read_refresh_claims,
)


class TokenClientClaimTests(unittest.TestCase):
    def test_plain_tokens_carry_no_client(self) -> None:
        user_id = uuid.uuid4()
        claims = read_access_claims(create_access_token(user_id))

        self.assertIsNotNone(claims)
        self.assertEqual(claims.user_id, user_id)
        self.assertIsNone(claims.client)
        self.assertIsNone(claims.key_version)
        self.assertNotIn("cli", decode_token(create_access_token(user_id)))

    def test_client_tokens_carry_client_and_key_version(self) -> None:
        user_id = uuid.uuid4()
        token = create_access_token(user_id, client="work", key_version="abc123def456")
        payload = decode_token(token)

        self.assertEqual(payload["cli"], "work")
        self.assertEqual(payload["ckv"], "abc123def456")
        claims = read_access_claims(token)
        self.assertEqual(claims.client, "work")
        self.assertEqual(claims.key_version, "abc123def456")

    def test_access_reader_rejects_refresh_tokens(self) -> None:
        self.assertIsNone(read_access_claims(create_refresh_token(uuid.uuid4())))

    def test_refresh_reader_reads_client(self) -> None:
        token = create_refresh_token(uuid.uuid4(), client="work", key_version="k" * 12)
        claims = read_refresh_claims(token)

        self.assertEqual(claims.client, "work")
        self.assertEqual(claims.key_version, "k" * 12)

    def test_garbage_is_rejected(self) -> None:
        self.assertIsNone(read_access_claims("not-a-jwt"))
        self.assertIsNone(read_refresh_claims("not-a-jwt"))
