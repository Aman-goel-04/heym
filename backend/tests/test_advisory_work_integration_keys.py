"""Heym Work integration key, URL rules, key cache and SSO codes."""

import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.secret_tokens import hash_secret
from app.services.work_integration import (
    WorkUrlError,
    _KeySnapshot,
    clear_key_cache,
    consume_sso_code,
    create_sso_code,
    key_matches,
    key_version,
    mint_work_key,
    normalize_work_url,
    sso_callback_target,
    valid_work_state,
    work_token_is_bound,
)


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class WorkKeyShapeTests(unittest.TestCase):
    def test_key_has_prefix_and_is_not_digest_shaped(self) -> None:
        key = mint_work_key()

        self.assertTrue(key.startswith("hwi_"))
        self.assertNotRegex(key, r"^[0-9a-f]{64}$")
        self.assertGreaterEqual(len(key), 40)

    def test_keys_are_unique(self) -> None:
        self.assertNotEqual(mint_work_key(), mint_work_key())


class KeyMatchTests(unittest.TestCase):
    def test_real_key_matches_its_digest(self) -> None:
        key = mint_work_key()
        self.assertTrue(key_matches(hash_secret(key), key))

    def test_stored_digest_is_not_a_credential(self) -> None:
        stored = hash_secret(mint_work_key())
        self.assertFalse(key_matches(stored, stored))

    def test_missing_values_never_match(self) -> None:
        self.assertFalse(key_matches(None, "hwi_x"))
        self.assertFalse(key_matches(hash_secret("hwi_x"), None))
        self.assertFalse(key_matches(hash_secret("hwi_x"), ""))


class WorkUrlTests(unittest.TestCase):
    def test_https_url_is_kept_without_trailing_slash(self) -> None:
        self.assertEqual(
            normalize_work_url(" https://work.acme.example/ "), "https://work.acme.example"
        )

    def test_path_prefix_is_allowed(self) -> None:
        self.assertEqual(
            normalize_work_url("https://acme.example/work"), "https://acme.example/work"
        )

    def test_http_is_allowed_only_for_local_hosts(self) -> None:
        self.assertEqual(normalize_work_url("http://localhost:4018"), "http://localhost:4018")
        with self.assertRaises(WorkUrlError):
            normalize_work_url("http://work.acme.example")

    def test_query_and_fragment_are_rejected(self) -> None:
        with self.assertRaises(WorkUrlError):
            normalize_work_url("https://work.acme.example/?next=evil")
        with self.assertRaises(WorkUrlError):
            normalize_work_url("https://work.acme.example/#x")

    def test_empty_clears_the_url(self) -> None:
        self.assertEqual(normalize_work_url("   "), "")


class WorkStateTests(unittest.TestCase):
    def test_state_shape(self) -> None:
        self.assertTrue(valid_work_state("a" * 16))
        self.assertTrue(valid_work_state("Ab-_" * 8))
        self.assertFalse(valid_work_state("short"))
        self.assertFalse(valid_work_state("bad state with spaces!!"))
        self.assertFalse(valid_work_state(None))

    def test_callback_target_uses_the_registered_url(self) -> None:
        target = sso_callback_target("https://work.acme.example", "code123", "s" * 20)
        self.assertEqual(
            target, f"https://work.acme.example/auth/callback?code=code123&state={'s' * 20}"
        )


class SsoCodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_code_is_stored_as_digest_with_short_expiry(self) -> None:
        db = AsyncMock()
        db.add = MagicMock()
        before = datetime.now(timezone.utc)

        code = await create_sso_code(db, uuid.uuid4())

        stored = db.add.call_args.args[0]
        self.assertEqual(stored.code_hash, hash_secret(code))
        self.assertNotEqual(stored.code_hash, code)
        self.assertLessEqual(stored.expires_at - before, timedelta(seconds=61))

    async def test_consuming_marks_the_code_used(self) -> None:
        user_id = uuid.uuid4()
        row = SimpleNamespace(user_id=user_id, used_at=None)
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_ScalarResult(row))

        result = await consume_sso_code(db, "c" * 43)

        self.assertEqual(result, user_id)
        self.assertIsNotNone(row.used_at)

    async def test_unknown_used_or_expired_code_returns_none(self) -> None:
        statements: list[object] = []

        async def capture(statement: object) -> _ScalarResult:
            statements.append(statement)
            return _ScalarResult(None)

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=capture)

        self.assertIsNone(await consume_sso_code(db, "c" * 43))
        compiled = str(statements[0])
        self.assertIn("used_at IS NULL", compiled)
        self.assertIn("expires_at >", compiled)


class WorkTokenBindingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_key_cache()

    def tearDown(self) -> None:
        clear_key_cache()

    async def test_bound_token_needs_the_current_key_and_version(self) -> None:
        key = mint_work_key()
        digest = hash_secret(key)
        snapshot = _KeySnapshot(enabled=True, key_hash=digest, loaded_at=time.monotonic())
        with patch(
            "app.services.work_integration._load_snapshot", AsyncMock(return_value=snapshot)
        ):
            self.assertTrue(await work_token_is_bound(key, key_version(digest)))
            self.assertFalse(await work_token_is_bound(key, "000000000000"))
            self.assertFalse(await work_token_is_bound(None, key_version(digest)))
            self.assertFalse(await work_token_is_bound(digest, key_version(digest)))

    async def test_disabled_integration_binds_nothing(self) -> None:
        key = mint_work_key()
        digest = hash_secret(key)
        snapshot = _KeySnapshot(enabled=False, key_hash=digest, loaded_at=time.monotonic())
        with patch(
            "app.services.work_integration._load_snapshot", AsyncMock(return_value=snapshot)
        ):
            self.assertFalse(await work_token_is_bound(key, key_version(digest)))

    async def test_snapshot_is_cached_briefly(self) -> None:
        snapshot = _KeySnapshot(enabled=False, key_hash=None, loaded_at=time.monotonic())
        loader = AsyncMock(return_value=snapshot)
        with patch("app.services.work_integration._load_snapshot", loader):
            await work_token_is_bound("hwi_a", "x")
            await work_token_is_bound("hwi_b", "y")

        self.assertEqual(loader.await_count, 1)
