"""A Work-bound token authenticates only together with the current integration key."""

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.auth import refresh_tokens
from app.api.deps import get_current_user, get_current_user_optional, resolve_token_user_id
from app.models.schemas import TokenRefresh
from app.services.auth import create_access_token, create_refresh_token

_BOUND = "app.api.deps.work_token_is_bound"


class _Request:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}
        self.cookies: dict[str, str] = {}


def _work_token(user_id: uuid.UUID) -> str:
    return create_access_token(user_id, client="work", key_version="abc123def456")


class ResolveTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_token_needs_no_key(self) -> None:
        user_id = uuid.uuid4()
        with patch(_BOUND, AsyncMock()) as bound:
            result = await resolve_token_user_id(_Request(), create_access_token(user_id))

        self.assertEqual(result, user_id)
        bound.assert_not_awaited()

    async def test_work_token_without_key_is_rejected(self) -> None:
        with patch(_BOUND, AsyncMock(return_value=False)) as bound:
            result = await resolve_token_user_id(_Request(), _work_token(uuid.uuid4()))

        self.assertIsNone(result)
        bound.assert_awaited_once_with(None, "abc123def456")

    async def test_work_token_with_bound_key_is_accepted(self) -> None:
        user_id = uuid.uuid4()
        request = _Request({"X-Heym-Work-Key": "hwi_presented"})
        with patch(_BOUND, AsyncMock(return_value=True)) as bound:
            result = await resolve_token_user_id(request, _work_token(user_id))

        self.assertEqual(result, user_id)
        bound.assert_awaited_once_with("hwi_presented", "abc123def456")

    async def test_unknown_client_is_rejected(self) -> None:
        token = create_access_token(uuid.uuid4(), client="other", key_version="x")
        with patch(_BOUND, AsyncMock(return_value=True)) as bound:
            self.assertIsNone(await resolve_token_user_id(_Request(), token))

        bound.assert_not_awaited()

    async def test_refresh_token_is_not_an_access_token(self) -> None:
        token = create_refresh_token(uuid.uuid4())
        self.assertIsNone(await resolve_token_user_id(_Request(), token))


class DependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_user_is_none_for_an_unbound_work_token(self) -> None:
        db = AsyncMock()
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=_work_token(uuid.uuid4())
        )
        with patch(_BOUND, AsyncMock(return_value=False)):
            result = await get_current_user_optional(_Request(), credentials, db)

        self.assertIsNone(result)
        db.execute.assert_not_awaited()

    async def test_current_user_rejects_an_unbound_work_token(self) -> None:
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=_work_token(uuid.uuid4())
        )
        with patch(_BOUND, AsyncMock(return_value=False)):
            with self.assertRaises(HTTPException) as ctx:
                await get_current_user(_Request(), credentials, AsyncMock())

        self.assertEqual(ctx.exception.status_code, 401)


class PublicRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_refresh_rejects_a_work_refresh_token(self) -> None:
        token = create_refresh_token(uuid.uuid4(), client="work", key_version="abc123def456")
        db = AsyncMock()
        request = SimpleNamespace(cookies={}, headers={})
        response = SimpleNamespace(set_cookie=lambda **_: None)

        with self.assertRaises(HTTPException) as ctx:
            await refresh_tokens(TokenRefresh(refresh_token=token), request, response, db=db)

        self.assertEqual(ctx.exception.status_code, 401)
        db.execute.assert_not_awaited()
