"""Regression coverage for the board card chain race (heymrun/heym#644).

enqueue_card_chain used to check-then-write with a gap: the "is a chain already
running" check was a plain SELECT, and the first BoardCardRun row was only created
later, inside the spawned background task. That meant even two sequential requests
(the second sent after the first already returned) could both pass the check while
the first task was still queued, and start two chains for the same card.

The fix claims the card inside the request's own transaction: it locks the card row
with SELECT ... FOR UPDATE, then creates and commits the first run row before the
background task is ever spawned. These tests run against real PostgreSQL because the
guarantee is a property of row locking under true concurrency, not something a mock
can demonstrate.
"""

import asyncio
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import delete, select

from app.db.models import (
    Board,
    BoardCard,
    BoardCardRun,
    BoardColumn,
    BoardColumnWorkflow,
    User,
    Workflow,
)
from app.db.session import async_session_maker, engine
from app.services import board_run_service


class _RealBoardFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.workflow_id = uuid.uuid4()
        self.board_id = uuid.uuid4()
        self.column_id = uuid.uuid4()
        self.empty_column_id = uuid.uuid4()
        self.card_id = uuid.uuid4()

        async with async_session_maker() as db:
            db.add(
                User(
                    id=self.user_id,
                    email=f"board-race-{uuid.uuid4()}@example.com",
                    hashed_password="pw",
                    name="Board Race Test User",
                )
            )
            db.add(
                Workflow(
                    id=self.workflow_id,
                    owner_id=self.user_id,
                    name="Board Race Test Workflow",
                    nodes=[],
                    edges=[],
                )
            )
            await db.flush()
            db.add(Board(id=self.board_id, owner_id=self.user_id, name="Board Race Test Board"))
            await db.flush()
            db.add(
                BoardColumn(id=self.column_id, board_id=self.board_id, name="Planning", position=0)
            )
            db.add(
                BoardColumn(
                    id=self.empty_column_id, board_id=self.board_id, name="Backlog", position=1
                )
            )
            await db.flush()
            db.add(
                BoardColumnWorkflow(
                    column_id=self.column_id, workflow_id=self.workflow_id, position=0
                )
            )
            db.add(
                BoardCard(
                    id=self.card_id,
                    board_id=self.board_id,
                    column_id=self.column_id,
                    title="Race test card",
                    run_status="idle",
                )
            )
            await db.commit()

        self.card = SimpleNamespace(id=self.card_id, run_status="idle")
        self.column = SimpleNamespace(id=self.column_id)
        self.empty_column = SimpleNamespace(id=self.empty_column_id)
        self.board = SimpleNamespace(id=self.board_id, owner_id=self.user_id)

    async def asyncTearDown(self) -> None:
        async with async_session_maker() as db:
            await db.execute(delete(BoardCardRun).where(BoardCardRun.card_id == self.card_id))
            await db.execute(delete(BoardCard).where(BoardCard.id == self.card_id))
            await db.execute(
                delete(BoardColumnWorkflow).where(BoardColumnWorkflow.column_id == self.column_id)
            )
            await db.execute(
                delete(BoardColumn).where(
                    BoardColumn.id.in_([self.column_id, self.empty_column_id])
                )
            )
            await db.execute(delete(Board).where(Board.id == self.board_id))
            await db.execute(delete(Workflow).where(Workflow.id == self.workflow_id))
            await db.execute(delete(User).where(User.id == self.user_id))
            await db.commit()
        await engine.dispose()


class ConcurrentEnqueueRealPostgresTests(_RealBoardFixture):
    async def test_concurrent_enqueue_only_one_chain_starts(self) -> None:
        async def _attempt() -> str:
            async with async_session_maker() as db:
                with patch.object(board_run_service, "_spawn_chain", MagicMock()):
                    return await board_run_service.enqueue_card_chain(
                        db,
                        card=self.card,
                        column=self.column,
                        board=self.board,
                        move=None,
                        rerun=True,
                    )

        results = await asyncio.gather(*[_attempt() for _ in range(8)])

        self.assertEqual(results.count(board_run_service.ENQUEUE_STARTED), 1)
        self.assertEqual(results.count(board_run_service.ENQUEUE_BLOCKED), 7)

        async with async_session_maker() as db:
            runs = (
                (await db.execute(select(BoardCardRun).where(BoardCardRun.card_id == self.card_id)))
                .scalars()
                .all()
            )
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, "running")

    async def test_sequential_enqueue_second_call_is_blocked(self) -> None:
        """The bug this closes: two sequential (not concurrent) requests both used to
        pass the check, because the first run row did not exist until the background
        task got around to creating it. Claiming the row before returning closes that
        window even when the two calls are strictly one-after-another."""
        with patch.object(board_run_service, "_spawn_chain", MagicMock()):
            async with async_session_maker() as db:
                first = await board_run_service.enqueue_card_chain(
                    db,
                    card=self.card,
                    column=self.column,
                    board=self.board,
                    move=None,
                    rerun=True,
                )
            async with async_session_maker() as db:
                second = await board_run_service.enqueue_card_chain(
                    db,
                    card=self.card,
                    column=self.column,
                    board=self.board,
                    move=None,
                    rerun=True,
                )

        self.assertEqual(first, board_run_service.ENQUEUE_STARTED)
        self.assertEqual(second, board_run_service.ENQUEUE_BLOCKED)

        async with async_session_maker() as db:
            runs = (
                (await db.execute(select(BoardCardRun).where(BoardCardRun.card_id == self.card_id)))
                .scalars()
                .all()
            )
        self.assertEqual(len(runs), 1)

    async def test_move_into_column_without_a_chain_is_not_blocked(self) -> None:
        with patch.object(board_run_service, "_auto_advance", AsyncMock()):
            async with async_session_maker() as db:
                result = await board_run_service.enqueue_card_chain(
                    db,
                    card=self.card,
                    column=self.empty_column,
                    board=self.board,
                    move=None,
                    rerun=False,
                )

        self.assertEqual(result, board_run_service.ENQUEUE_NO_CHAIN)

        async with async_session_maker() as db:
            runs = (
                (await db.execute(select(BoardCardRun).where(BoardCardRun.card_id == self.card_id)))
                .scalars()
                .all()
            )
        self.assertEqual(runs, [])


if __name__ == "__main__":
    unittest.main()
