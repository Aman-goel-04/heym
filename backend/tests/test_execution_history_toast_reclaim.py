"""Weekly TOAST reclaim for execution_history.

DELETE never shrinks the TOAST file that holds node_results; the weekly pass
rewrites the table only when that file is genuinely bloated, never when it is
merely large. These tests pin the decision, the weekly claim, error
containment, and, against a local PostgreSQL, that a rewrite keeps every row.
"""

import asyncio
import base64
import dataclasses
import os
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import asyncpg
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url

from app.config import settings
from app.db.models import CronCleanupClaim, ExecutionHistory, User, Workflow
from app.db.session import async_session_maker, engine, libpq_dsn
from app.services.cron_scheduler import CronScheduler
from app.services.cron_slot_state import claim_cleanup_slot, iso_week_slot
from app.services.execution_history_maintenance import (
    REWRITE_LOCK_TIMEOUT_MS,
    REWRITE_STATEMENT_TIMEOUT_MS,
    TOAST_CHUNK_PAYLOAD_BYTES,
    TOAST_REWRITE_MIN_RATIO,
    TOAST_REWRITE_MIN_WASTED_BYTES,
    ToastBloatStats,
    ToastReclaimOutcome,
    read_toast_bloat_stats,
    reclaim_execution_history_toast,
    should_rewrite_toast,
)

MB = 1024 * 1024
GB = 1024 * MB


def _measured(toast_bytes: int, toast_live_tuples: int) -> ToastBloatStats:
    return ToastBloatStats(
        toast_bytes=toast_bytes,
        toast_live_tuples=toast_live_tuples,
        live_count_measured=True,
    )


class ShouldRewriteToastTests(unittest.TestCase):
    """The pure bloat decision: 10x the live payload AND 1 GB of waste."""

    def test_production_thresholds_are_ten_times_and_one_gigabyte(self) -> None:
        self.assertEqual(TOAST_REWRITE_MIN_RATIO, 10)
        self.assertEqual(TOAST_REWRITE_MIN_WASTED_BYTES, GB)
        self.assertEqual(TOAST_CHUNK_PAYLOAD_BYTES, 2000)

    def test_rewrites_the_observed_incident(self) -> None:
        # Production before the manual rewrite: 7042 MB for ~137,000 live chunks.
        stats = _measured(toast_bytes=7042 * MB, toast_live_tuples=137_000)

        self.assertGreater(stats.toast_bytes / stats.live_payload_bytes, 26)
        self.assertTrue(should_rewrite_toast(stats))

    def test_skips_the_healthy_file_left_by_the_manual_rewrite(self) -> None:
        # Production after VACUUM FULL: 873 MB for the same live chunks (~3.3x).
        stats = _measured(toast_bytes=873 * MB, toast_live_tuples=137_000)

        self.assertFalse(should_rewrite_toast(stats))

    def test_skips_a_healthy_ratio_even_when_its_waste_exceeds_one_gigabyte(self) -> None:
        # A packed file stays at ~3.3x the live payload. Once the table grows,
        # that healthy slack alone passes 1 GB; a 3x rule would lock it weekly.
        stats = _measured(toast_bytes=int(3.3 * 600_000 * 2000), toast_live_tuples=600_000)

        self.assertGreater(stats.wasted_bytes, GB)
        self.assertFalse(should_rewrite_toast(stats))

    def test_skips_below_ten_times_even_with_many_gigabytes_wasted(self) -> None:
        stats = _measured(toast_bytes=9 * 1_000_000 * 2000, toast_live_tuples=1_000_000)

        self.assertGreater(stats.wasted_bytes, 10 * GB)
        self.assertFalse(should_rewrite_toast(stats))

    def test_skips_a_high_ratio_when_the_waste_is_under_one_gigabyte(self) -> None:
        # 50x bloated, but only ~98 MB to win back: not worth a table lock.
        stats = _measured(toast_bytes=100_000_000, toast_live_tuples=1_000)

        self.assertGreaterEqual(stats.toast_bytes / stats.live_payload_bytes, 10)
        self.assertLess(stats.wasted_bytes, GB)
        self.assertFalse(should_rewrite_toast(stats))

    def test_both_thresholds_are_inclusive(self) -> None:
        stats = _measured(toast_bytes=10 * 2_000_000, toast_live_tuples=1_000)

        self.assertTrue(
            should_rewrite_toast(stats, min_ratio=10, min_wasted_bytes=stats.wasted_bytes)
        )
        self.assertFalse(
            should_rewrite_toast(stats, min_ratio=10, min_wasted_bytes=stats.wasted_bytes + 1)
        )
        self.assertFalse(
            should_rewrite_toast(stats, min_ratio=10.01, min_wasted_bytes=stats.wasted_bytes)
        )

    def test_rewrites_a_large_file_with_no_live_chunks_left(self) -> None:
        # Every row was cleared, but the file still holds the old high-water mark.
        stats = _measured(toast_bytes=2 * GB, toast_live_tuples=0)

        self.assertTrue(should_rewrite_toast(stats))

    def test_skips_when_the_live_count_was_not_measured_by_a_vacuum(self) -> None:
        # After a crash or pg_stat_reset(), n_live_tup only counts inserts since
        # the reset, so a compact file would look almost entirely wasted.
        stats = ToastBloatStats(
            toast_bytes=7042 * MB, toast_live_tuples=40, live_count_measured=False
        )

        self.assertFalse(should_rewrite_toast(stats))

    def test_decision_input_is_the_toast_chunk_count_not_the_table_row_count(self) -> None:
        # 45,000 history rows whose node_results span 600,000 chunks, packed at
        # ~3.3x. Measured per table row this file would look 44x bloated.
        table_rows = 45_000
        stats = _measured(toast_bytes=int(3.3 * 600_000 * 2000), toast_live_tuples=600_000)

        self.assertEqual(
            {field.name for field in dataclasses.fields(ToastBloatStats)},
            {"toast_bytes", "toast_live_tuples", "live_count_measured"},
        )
        self.assertGreater(stats.toast_bytes / (table_rows * TOAST_CHUNK_PAYLOAD_BYTES), 10)
        self.assertFalse(should_rewrite_toast(stats))


class ReadToastBloatStatsTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_size_and_live_tuples_of_the_toast_relation(self) -> None:
        db = AsyncMock()
        row = SimpleNamespace(toast_bytes=7042 * MB, toast_live_tuples=137_000, measured=True)
        db.execute.return_value = SimpleNamespace(first=lambda: row)

        stats = await read_toast_bloat_stats(db)

        self.assertEqual(
            stats,
            ToastBloatStats(
                toast_bytes=7042 * MB, toast_live_tuples=137_000, live_count_measured=True
            ),
        )
        sql = " ".join(str(db.execute.await_args.args[0]).split())
        self.assertIn("pg_relation_size(c.reltoastrelid)", sql)
        self.assertIn("pg_stat_all_tables s ON s.relid = c.reltoastrelid", sql)

    async def test_missing_toast_statistics_count_as_unmeasured(self) -> None:
        db = AsyncMock()
        row = SimpleNamespace(toast_bytes=8192, toast_live_tuples=None, measured=None)
        db.execute.return_value = SimpleNamespace(first=lambda: row)

        stats = await read_toast_bloat_stats(db)

        self.assertEqual(
            stats, ToastBloatStats(toast_bytes=8192, toast_live_tuples=0, live_count_measured=False)
        )

    async def test_returns_none_without_a_toast_relation(self) -> None:
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(first=lambda: None)

        self.assertIsNone(await read_toast_bloat_stats(db))


class ReclaimExecutionHistoryToastTests(unittest.IsolatedAsyncioTestCase):
    """The pass reads stats on a closed-out session, then vacuums on its own connection."""

    def setUp(self) -> None:
        self.events: list[str] = []
        self.session = AsyncMock()
        self.session.__aenter__.return_value = self.session

        async def _exit(*_args: object) -> bool:
            self.events.append("decision session closed")
            return False

        self.session.__aexit__.side_effect = _exit
        self.connection = AsyncMock()

        async def _connect(*_args: object, **_kwargs: object) -> AsyncMock:
            self.events.append("vacuum connection opened")
            return self.connection

        self.connect = AsyncMock(side_effect=_connect)

    async def _reclaim(
        self, stats: ToastBloatStats | None, **thresholds: float
    ) -> ToastReclaimOutcome:
        with (
            patch(
                "app.services.execution_history_maintenance.async_session_maker",
                return_value=self.session,
            ),
            patch(
                "app.services.execution_history_maintenance.read_toast_bloat_stats",
                AsyncMock(return_value=stats),
            ),
            patch("app.services.execution_history_maintenance.asyncpg.connect", self.connect),
        ):
            return await reclaim_execution_history_toast(**thresholds)

    async def test_compact_table_is_never_locked(self) -> None:
        outcome = await self._reclaim(_measured(toast_bytes=873 * MB, toast_live_tuples=137_000))

        self.assertEqual(outcome, ToastReclaimOutcome.SKIPPED)
        self.connect.assert_not_awaited()

    async def test_unmeasured_or_missing_statistics_are_never_acted_on(self) -> None:
        unmeasured = ToastBloatStats(
            toast_bytes=7042 * MB, toast_live_tuples=0, live_count_measured=False
        )

        self.assertEqual(await self._reclaim(unmeasured), ToastReclaimOutcome.SKIPPED)
        self.assertEqual(await self._reclaim(None), ToastReclaimOutcome.SKIPPED)
        self.connect.assert_not_awaited()

    async def test_bloated_table_is_vacuumed_full_on_its_own_connection(self) -> None:
        outcome = await self._reclaim(_measured(toast_bytes=7042 * MB, toast_live_tuples=137_000))

        self.assertEqual(outcome, ToastReclaimOutcome.REWRITTEN)
        self.connection.execute.assert_awaited_once_with("VACUUM (FULL) execution_history")
        self.connection.close.assert_awaited_once()
        # The decision transaction must be over before VACUUM asks for its lock.
        self.assertEqual(self.events[:2], ["decision session closed", "vacuum connection opened"])

    async def test_timeouts_are_set_on_the_vacuum_connection_only(self) -> None:
        await self._reclaim(_measured(toast_bytes=7042 * MB, toast_live_tuples=137_000))

        args, kwargs = self.connect.await_args
        self.assertEqual(args, (libpq_dsn(),))
        server_settings = kwargs["server_settings"]
        self.assertEqual(server_settings["lock_timeout"], str(REWRITE_LOCK_TIMEOUT_MS))
        self.assertEqual(server_settings["statement_timeout"], str(REWRITE_STATEMENT_TIMEOUT_MS))
        self.assertIn("application_name", server_settings)
        self.connection.execute.assert_awaited_once()

    def test_lock_timeout_is_seconds_and_the_rewrite_ends_inside_the_cron_grace(self) -> None:
        self.assertLessEqual(REWRITE_LOCK_TIMEOUT_MS, 10_000)
        # A rewrite that outlives the misfire grace would make the scheduler
        # drop the cron slots it held up.
        self.assertLess(
            REWRITE_LOCK_TIMEOUT_MS + REWRITE_STATEMENT_TIMEOUT_MS,
            settings.cron_misfire_grace_seconds * 1000,
        )
        self.assertGreaterEqual(REWRITE_STATEMENT_TIMEOUT_MS, 60_000)

    async def test_lock_timeout_skips_the_week_without_raising(self) -> None:
        self.connection.execute.side_effect = asyncpg.exceptions.LockNotAvailableError(
            "canceling statement due to lock timeout"
        )

        with self.assertLogs("cron_scheduler", level="WARNING"):
            outcome = await self._reclaim(
                _measured(toast_bytes=7042 * MB, toast_live_tuples=137_000)
            )

        self.assertEqual(outcome, ToastReclaimOutcome.BUSY)
        self.connection.close.assert_awaited_once()

    async def test_injected_thresholds_drive_the_decision(self) -> None:
        small = _measured(toast_bytes=64 * 8192, toast_live_tuples=10)

        self.assertEqual(await self._reclaim(small), ToastReclaimOutcome.SKIPPED)
        self.assertEqual(
            await self._reclaim(small, min_ratio=0.0, min_wasted_bytes=0),
            ToastReclaimOutcome.REWRITTEN,
        )


class IsoWeekSlotTests(unittest.TestCase):
    def test_key_is_iso_year_and_week_and_fits_the_slot_column(self) -> None:
        self.assertEqual(iso_week_slot(datetime(2026, 9, 28, 4, 0)), "2026-W40")
        self.assertEqual(iso_week_slot(datetime(2026, 10, 4, 4, 0)), "2026-W40")
        self.assertEqual(iso_week_slot(datetime(2026, 10, 5, 4, 0)), "2026-W41")
        # The ISO year, not the calendar year, owns the first days of January.
        self.assertEqual(iso_week_slot(datetime(2027, 1, 1, 4, 0)), "2026-W53")
        self.assertLessEqual(len(iso_week_slot(datetime(2026, 1, 5))), 8)


class _FrozenDatetime(datetime):
    frozen: datetime = datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
        return cls.frozen.astimezone(tz) if tz is not None else cls.frozen


class _ClaimTable:
    """Stands in for cron_cleanup_claims: the first insert of a key wins."""

    def __init__(self) -> None:
        self.claimed: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, str]] = []

    async def __call__(
        self, *, job_name: str, slot_date: str, worker_id: str | None = None
    ) -> bool:
        key = (job_name, slot_date)
        self.calls.append(key)
        if key in self.claimed:
            return False
        self.claimed.add(key)
        return True


class WeeklyToastReclaimSchedulingTests(unittest.IsolatedAsyncioTestCase):
    TZ = ZoneInfo("Europe/Istanbul")

    def setUp(self) -> None:
        self.claims = _ClaimTable()
        self.reclaim = AsyncMock(return_value=ToastReclaimOutcome.REWRITTEN)

    async def _pass(self, scheduler: CronScheduler, at: datetime) -> None:
        _FrozenDatetime.frozen = at
        with (
            patch("app.services.cron_scheduler.datetime", _FrozenDatetime),
            patch("app.services.cron_scheduler.get_configured_timezone", return_value=self.TZ),
            patch("app.services.cron_scheduler.claim_cleanup_slot", self.claims),
            patch("app.services.cron_scheduler.reclaim_execution_history_toast", self.reclaim),
        ):
            await scheduler._check_execution_history_toast_reclaim()

    def _local(self, *args: int) -> datetime:
        return datetime(*args, tzinfo=self.TZ)

    async def test_runs_inside_the_four_oclock_window_of_the_configured_timezone(self) -> None:
        scheduler = CronScheduler()

        # 01:10 UTC is 04:10 in Istanbul.
        await self._pass(scheduler, datetime(2026, 9, 28, 1, 10, tzinfo=timezone.utc))

        self.assertEqual(self.claims.calls, [("execution_history_toast_reclaim", "2026-W40")])
        self.reclaim.assert_awaited_once_with()

    async def test_does_nothing_outside_the_four_oclock_window(self) -> None:
        scheduler = CronScheduler()

        for moment in [
            self._local(2026, 9, 28, 3, 59),
            self._local(2026, 9, 28, 4, 30),
            self._local(2026, 9, 28, 5, 0),
            self._local(2026, 9, 28, 16, 10),
            datetime(2026, 9, 28, 4, 10, tzinfo=timezone.utc),  # 07:10 in Istanbul
        ]:
            await self._pass(scheduler, moment)

        self.assertEqual(self.claims.calls, [])
        self.reclaim.assert_not_awaited()

    async def test_runs_once_per_iso_week(self) -> None:
        scheduler = CronScheduler()

        await self._pass(scheduler, self._local(2026, 9, 28, 4, 0))
        await self._pass(scheduler, self._local(2026, 9, 28, 4, 0, 30))
        await self._pass(scheduler, self._local(2026, 9, 29, 4, 10))
        await self._pass(scheduler, self._local(2026, 10, 4, 4, 29))
        self.assertEqual(self.reclaim.await_count, 1)

        await self._pass(scheduler, self._local(2026, 10, 5, 4, 0))

        self.assertEqual(self.reclaim.await_count, 2)
        self.assertEqual([slot for _job, slot in self.claims.calls], ["2026-W40", "2026-W41"])

    async def test_a_restarted_or_second_instance_does_not_rewrite_the_same_week(self) -> None:
        first, second = CronScheduler(), CronScheduler()

        await self._pass(first, self._local(2026, 9, 28, 4, 0))
        await self._pass(second, self._local(2026, 9, 28, 4, 0))
        await self._pass(second, self._local(2026, 9, 30, 4, 5))

        self.reclaim.assert_awaited_once()
        self.assertEqual(len(self.claims.calls), 2)

    async def test_lost_claim_skips_the_rewrite_and_settles_the_week(self) -> None:
        self.claims.claimed.add(("execution_history_toast_reclaim", "2026-W40"))
        scheduler = CronScheduler()

        await self._pass(scheduler, self._local(2026, 9, 28, 4, 0))
        await self._pass(scheduler, self._local(2026, 9, 28, 4, 1))

        self.reclaim.assert_not_awaited()
        self.assertEqual(len(self.claims.calls), 1)

    async def test_slot_is_claimed_before_the_rewrite_starts(self) -> None:
        order: list[str] = []
        claims = self.claims

        async def _claim(**kwargs: str) -> bool:
            order.append("claim")
            return await claims(**kwargs)

        async def _reclaim() -> ToastReclaimOutcome:
            order.append("rewrite")
            return ToastReclaimOutcome.REWRITTEN

        self.claims = _claim  # type: ignore[assignment]
        self.reclaim = AsyncMock(side_effect=_reclaim)

        await self._pass(CronScheduler(), self._local(2026, 9, 28, 4, 0))

        self.assertEqual(order, ["claim", "rewrite"])

    async def test_failed_rewrite_is_contained_and_not_retried_this_week(self) -> None:
        scheduler = CronScheduler()
        for error in [
            asyncpg.exceptions.LockNotAvailableError("canceling statement due to lock timeout"),
            asyncpg.exceptions.QueryCanceledError("canceling statement due to statement timeout"),
            RuntimeError("could not extend file: No space left on device"),
        ]:
            with self.subTest(error=type(error).__name__):
                self.reclaim.reset_mock()
                self.reclaim.side_effect = error
                week_start = {
                    "LockNotAvailableError": (2026, 9, 28),
                    "QueryCanceledError": (2026, 10, 5),
                    "RuntimeError": (2026, 10, 12),
                }[type(error).__name__]

                with self.assertLogs("cron_scheduler", level="ERROR"):
                    await self._pass(scheduler, self._local(*week_start, 4, 0))
                await self._pass(scheduler, self._local(*week_start, 4, 5))

                self.reclaim.assert_awaited_once()

    async def test_run_loop_runs_the_reclaim_check_alongside_the_other_jobs(self) -> None:
        scheduler = CronScheduler()
        scheduler._running = True
        steps = [
            name
            for name in dir(CronScheduler)
            if name.startswith("_check_")
            or name in ("_maintain_run_queue", "_apply_automatic_weighting")
        ]
        mocks = {name: AsyncMock() for name in steps}

        async def _stop_after_one_pass(_seconds: float) -> None:
            scheduler._running = False

        with (
            patch.multiple(scheduler, **mocks),
            patch("app.services.cron_scheduler.lock_service", MagicMock(is_leader=True)),
            patch("app.services.cron_scheduler.asyncio.sleep", _stop_after_one_pass),
        ):
            await scheduler._run_loop()

        self.assertIn("_check_execution_history_toast_reclaim", steps)
        for name, mock in mocks.items():
            with self.subTest(step=name):
                mock.assert_awaited_once()


def _local_database() -> bool:
    return make_url(settings.database_url).host in {"localhost", "127.0.0.1", "::1"}


def _incompressible_node_results(size: int) -> list[dict]:
    # Random base64 defeats pglz, so the value is stored out of line in TOAST.
    blob = base64.b64encode(os.urandom(size * 3 // 4)).decode()
    return [{"node_id": "n1", "status": "success", "output": {"blob": blob}}]


@unittest.skipUnless(_local_database(), "VACUUM FULL tests only run against a local database")
class ExecutionHistoryToastReclaimRealPostgresTests(unittest.IsolatedAsyncioTestCase):
    """One pass with a tiny injected threshold rewrites a small table and keeps
    other users' rows; production thresholds leave the same table alone."""

    async def asyncSetUp(self) -> None:
        await engine.dispose()
        self.user_a, self.user_b = uuid.uuid4(), uuid.uuid4()
        self.workflow_a, self.workflow_b = uuid.uuid4(), uuid.uuid4()
        self.claim_job = f"test_toast_reclaim_{uuid.uuid4().hex[:8]}"
        async with async_session_maker() as db:
            for user_id, workflow_id in [
                (self.user_a, self.workflow_a),
                (self.user_b, self.workflow_b),
            ]:
                db.add(
                    User(
                        id=user_id,
                        email=f"toast_reclaim_{user_id.hex[:8]}@example.com",
                        hashed_password="test_hashed_password",
                        name="Toast Reclaim User",
                    )
                )
                db.add(
                    Workflow(
                        id=workflow_id,
                        name="Toast Reclaim Workflow",
                        owner_id=user_id,
                        nodes=[],
                        edges=[],
                    )
                )
                await db.flush()
            await db.commit()

    async def asyncTearDown(self) -> None:
        workflows = [self.workflow_a, self.workflow_b]
        async with async_session_maker() as db:
            await db.execute(
                delete(ExecutionHistory).where(ExecutionHistory.workflow_id.in_(workflows))
            )
            await db.execute(delete(Workflow).where(Workflow.id.in_(workflows)))
            await db.execute(delete(User).where(User.id.in_([self.user_a, self.user_b])))
            await db.execute(
                delete(CronCleanupClaim).where(CronCleanupClaim.job_name == self.claim_job)
            )
            await db.commit()
        await engine.dispose()

    async def _filenode(self) -> int:
        async with async_session_maker() as db:
            return int(
                (
                    await db.execute(text("SELECT pg_relation_filenode('execution_history')"))
                ).scalar_one()
            )

    async def _toast_bytes(self) -> int:
        async with async_session_maker() as db:
            stats = await read_toast_bloat_stats(db)
        assert stats is not None
        return stats.toast_bytes

    async def _vacuum_without_truncation(self) -> None:
        # Plain VACUUM measures the TOAST live count the decision reads. TRUNCATE
        # false keeps the freed pages in the file even when they sit at its end.
        connection = await asyncpg.connect(libpq_dsn())
        try:
            await connection.execute("VACUUM (TRUNCATE false) execution_history")
        finally:
            await connection.close()

    async def test_rewrite_keeps_other_users_rows_and_production_thresholds_skip(self) -> None:
        survivor_id = uuid.uuid4()
        survivor_results = _incompressible_node_results(100_000)
        async with async_session_maker() as db:
            for _ in range(20):
                db.add(
                    ExecutionHistory(
                        workflow_id=self.workflow_a,
                        status="success",
                        node_results=_incompressible_node_results(100_000),
                    )
                )
            await db.flush()
            db.add(
                ExecutionHistory(
                    id=survivor_id,
                    workflow_id=self.workflow_b,
                    status="success",
                    node_results=survivor_results,
                )
            )
            await db.commit()
        # User A clears their history; user B's run stays.
        async with async_session_maker() as db:
            await db.execute(
                delete(ExecutionHistory).where(ExecutionHistory.workflow_id == self.workflow_a)
            )
            await db.commit()
        await self._vacuum_without_truncation()

        async with async_session_maker() as db:
            stats = await read_toast_bloat_stats(db)
        assert stats is not None
        self.assertTrue(stats.live_count_measured)
        self.assertGreater(stats.wasted_bytes, 1 * MB)
        filenode_before = await self._filenode()
        toast_before = await self._toast_bytes()

        outcome = await reclaim_execution_history_toast(min_ratio=0.0, min_wasted_bytes=0)

        self.assertEqual(outcome, ToastReclaimOutcome.REWRITTEN)
        self.assertNotEqual(await self._filenode(), filenode_before)
        self.assertLess(await self._toast_bytes(), toast_before)
        async with async_session_maker() as db:
            survivor = (
                await db.execute(select(ExecutionHistory).where(ExecutionHistory.id == survivor_id))
            ).scalar_one()
            remaining_a = (
                await db.execute(
                    select(ExecutionHistory).where(ExecutionHistory.workflow_id == self.workflow_a)
                )
            ).all()
        self.assertEqual(survivor.node_results, survivor_results)
        self.assertEqual(remaining_a, [])

        filenode_after_rewrite = await self._filenode()
        outcome = await reclaim_execution_history_toast()

        self.assertEqual(outcome, ToastReclaimOutcome.SKIPPED)
        self.assertEqual(await self._filenode(), filenode_after_rewrite)

    async def test_weekly_key_claims_once_in_the_real_claim_table(self) -> None:
        week = iso_week_slot(datetime(2026, 9, 28, 4, 0))

        first, second = await asyncio.gather(
            claim_cleanup_slot(job_name=self.claim_job, slot_date=week, worker_id="instance-a"),
            claim_cleanup_slot(job_name=self.claim_job, slot_date=week, worker_id="instance-b"),
        )

        self.assertEqual(sorted([first, second]), [False, True])
