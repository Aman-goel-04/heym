"""PostgreSQL regression tests for eval test-case order_index assignment.

Guards the fix for the `0 or -1` truthiness bug: `max_idx_result.scalar() or -1`
silently treated a real max of 0 as "no rows", so every test case after the first
collided at order_index=0. Also guards the order_by tiebreaker added for suites
that already have duplicate order_index values on disk (no migration backfills
those; the relationship's order_by falls back to created_at, then id).
"""

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.evals import add_test_case, generate_suite_test_data
from app.db.models import EvalSuite, EvalTestCase, User
from app.db.session import async_session_maker, engine
from app.models.eval_schemas import EvalTestCaseCreate, GenerateTestDataRequest


class EvalOrderIndexTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await engine.dispose()

        self.owner_id = uuid.uuid4()
        self.suite_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add(
                User(
                    id=self.owner_id,
                    email=f"owner_{self.owner_id.hex[:8]}@example.com",
                    hashed_password="pw",
                    name="Suite Owner",
                )
            )
            session.add(
                EvalSuite(
                    id=self.suite_id,
                    owner_id=self.owner_id,
                    name="Order index test suite",
                )
            )
            await session.commit()

        self.current_user = SimpleNamespace(id=self.owner_id)

    async def asyncTearDown(self) -> None:
        from sqlalchemy import delete

        async with async_session_maker() as session:
            await session.execute(delete(EvalSuite).where(EvalSuite.id == self.suite_id))
            await session.execute(delete(User).where(User.id == self.owner_id))
            await session.commit()
        await engine.dispose()

    async def test_empty_suite_starts_at_zero(self) -> None:
        async with async_session_maker() as session:
            body = EvalTestCaseCreate(input="a", expected_output="b")
            response = await add_test_case(self.suite_id, body, self.current_user, session)
        self.assertEqual(response.order_index, 0)

    async def test_suite_whose_max_is_zero_gets_one_next(self) -> None:
        """The exact regression: a true max of 0 must not be treated as empty."""
        async with async_session_maker() as session:
            body = EvalTestCaseCreate(input="a", expected_output="b")
            first = await add_test_case(self.suite_id, body, self.current_user, session)
        self.assertEqual(first.order_index, 0)

        async with async_session_maker() as session:
            body = EvalTestCaseCreate(input="c", expected_output="d")
            second = await add_test_case(self.suite_id, body, self.current_user, session)
        self.assertEqual(second.order_index, 1)

    async def test_generate_after_one_existing_case_continues_at_one(self) -> None:
        async with async_session_maker() as session:
            body = EvalTestCaseCreate(input="a", expected_output="b")
            await add_test_case(self.suite_id, body, self.current_user, session)

        fake_cases = [
            {"input": "g1", "expected_output": "o1"},
            {"input": "g2", "expected_output": "o2"},
            {"input": "g3", "expected_output": "o3"},
        ]
        with patch("app.api.evals.generate_test_data", AsyncMock(return_value=fake_cases)):
            async with async_session_maker() as session:
                request = GenerateTestDataRequest(credential_id=uuid.uuid4(), count=3)
                await generate_suite_test_data(self.suite_id, request, self.current_user, session)

        async with async_session_maker() as session:
            result = await session.execute(
                select(EvalTestCase)
                .where(EvalTestCase.suite_id == self.suite_id)
                .order_by(EvalTestCase.order_index)
            )
            rows = result.scalars().all()

        order_indexes = [r.order_index for r in rows]
        self.assertEqual(order_indexes, [0, 1, 2, 3])

    async def test_duplicate_indexes_come_back_in_creation_order(self) -> None:
        """Simulates a pre-existing suite with duplicate order_index values from before
        the fix. No migration backfills these, so the relationship's order_by must break
        the tie using created_at (then id) instead of returning them in arbitrary order."""
        now = datetime.now(timezone.utc)
        older_id = uuid.uuid4()
        newer_id = uuid.uuid4()

        async with async_session_maker() as session:
            session.add(
                EvalTestCase(
                    id=older_id,
                    suite_id=self.suite_id,
                    input="older",
                    expected_output="x",
                    order_index=0,
                    created_at=now - timedelta(minutes=5),
                )
            )
            session.add(
                EvalTestCase(
                    id=newer_id,
                    suite_id=self.suite_id,
                    input="newer",
                    expected_output="y",
                    order_index=0,
                    created_at=now,
                )
            )
            await session.commit()

        async with async_session_maker() as session:
            result = await session.execute(
                select(EvalSuite)
                .where(EvalSuite.id == self.suite_id)
                .options(selectinload(EvalSuite.test_cases))
            )
            suite = result.scalar_one()

        ordered_ids = [tc.id for tc in suite.test_cases]
        self.assertEqual(ordered_ids, [older_id, newer_id])


if __name__ == "__main__":
    unittest.main()
