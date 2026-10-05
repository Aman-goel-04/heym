"""Weekly reclaim of the TOAST file behind ``execution_history``.

``node_results`` is large JSON stored out of line in the table's TOAST relation.
DELETE (including clear-all) removes rows but never shrinks that file:
autovacuum frees space inside it and keeps the file at its high-water mark, and
every later autovacuum pass still reads the whole sparse file. Production
reached 7 GB of TOAST for ~260 MB of live payload this way.

Only ``VACUUM (FULL)`` returns that space to the OS, and it holds an ACCESS
EXCLUSIVE lock on the table while it rewrites every remaining row. The weekly
pass therefore rewrites only a file that is genuinely bloated, never one that
is merely large, and gives up quickly when the table is busy.
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_maker, libpq_dsn, listener_server_settings

logger = logging.getLogger("cron_scheduler")

# Live payload per TOAST chunk (TOAST_MAX_CHUNK_SIZE is 1996 bytes on 8 KB pages).
TOAST_CHUNK_PAYLOAD_BYTES = 2000

# A freshly packed file already sits at ~3.3x the live payload (873 MB for
# ~260 MB after the production rewrite), so a low ratio would lock a healthy
# table every week once that slack passed 1 GB. The incident was ~27x.
TOAST_REWRITE_MIN_RATIO = 10.0
TOAST_REWRITE_MIN_WASTED_BYTES = 1024 * 1024 * 1024

# A busy table skips this week instead of queueing behind live queries, since
# every query behind a waiting ACCESS EXCLUSIVE request blocks as well.
REWRITE_LOCK_TIMEOUT_MS = 5_000
# The production rewrite of 7 GB took 2.7 s. The cap stays inside the cron
# misfire grace, so a slow rewrite cannot make the scheduler drop cron slots.
REWRITE_STATEMENT_TIMEOUT_MS = 5 * 60 * 1000

_TOAST_STATS_SQL = text(
    """
    SELECT
        pg_relation_size(c.reltoastrelid) AS toast_bytes,
        s.n_live_tup AS toast_live_tuples,
        (s.last_vacuum IS NOT NULL OR s.last_autovacuum IS NOT NULL) AS measured
    FROM pg_class c
    LEFT JOIN pg_stat_all_tables s ON s.relid = c.reltoastrelid
    WHERE c.oid = to_regclass('execution_history') AND c.reltoastrelid <> 0
    """
)


class ToastReclaimOutcome(str, Enum):
    REWRITTEN = "rewritten"
    SKIPPED = "skipped"
    BUSY = "busy"


@dataclass(frozen=True)
class ToastBloatStats:
    """Size and live chunk count of the ``execution_history`` TOAST relation.

    ``toast_live_tuples`` counts TOAST chunks, not history rows: one row's
    ``node_results`` can span hundreds of chunks. ``live_count_measured`` is
    False until a vacuum has counted the chunks since statistics were last
    reset; until then ``n_live_tup`` only tallies inserts since the reset.
    """

    toast_bytes: int
    toast_live_tuples: int
    live_count_measured: bool

    @property
    def live_payload_bytes(self) -> int:
        return self.toast_live_tuples * TOAST_CHUNK_PAYLOAD_BYTES

    @property
    def wasted_bytes(self) -> int:
        return self.toast_bytes - self.live_payload_bytes


def should_rewrite_toast(
    stats: ToastBloatStats,
    *,
    min_ratio: float = TOAST_REWRITE_MIN_RATIO,
    min_wasted_bytes: int = TOAST_REWRITE_MIN_WASTED_BYTES,
) -> bool:
    """Return True only when the TOAST file is both ``min_ratio`` times its live
    payload and wastes at least ``min_wasted_bytes``."""
    if not stats.live_count_measured:
        return False
    return (
        stats.toast_bytes >= min_ratio * stats.live_payload_bytes
        and stats.wasted_bytes >= min_wasted_bytes
    )


async def read_toast_bloat_stats(db: AsyncSession) -> ToastBloatStats | None:
    """Read the TOAST relation's file size and live chunk count.

    Returns None when ``execution_history`` or its TOAST relation does not exist.
    """
    row = (await db.execute(_TOAST_STATS_SQL)).first()
    if row is None:
        return None
    return ToastBloatStats(
        toast_bytes=int(row.toast_bytes),
        toast_live_tuples=int(row.toast_live_tuples or 0),
        live_count_measured=bool(row.measured),
    )


async def reclaim_execution_history_toast(
    *,
    min_ratio: float = TOAST_REWRITE_MIN_RATIO,
    min_wasted_bytes: int = TOAST_REWRITE_MIN_WASTED_BYTES,
) -> ToastReclaimOutcome:
    """Rewrite ``execution_history`` when its TOAST file is bloated.

    A compact table is left alone and takes no lock. A lock timeout returns
    BUSY; any other database error is raised for the caller to contain.
    """
    async with async_session_maker() as db:
        stats = await read_toast_bloat_stats(db)
    # The session is closed here: VACUUM deadlocks against a transaction of its
    # own caller that still holds a lock on the table.

    if stats is None:
        logger.info("execution_history has no TOAST relation; skipping TOAST reclaim")
        return ToastReclaimOutcome.SKIPPED
    if not stats.live_count_measured:
        logger.info(
            "execution_history TOAST live count not yet measured by a vacuum "
            "(%s file); skipping TOAST reclaim",
            _mb(stats.toast_bytes),
        )
        return ToastReclaimOutcome.SKIPPED
    if not should_rewrite_toast(stats, min_ratio=min_ratio, min_wasted_bytes=min_wasted_bytes):
        logger.info(
            "execution_history TOAST is compact (%s file, %s live, %s wasted); no rewrite",
            _mb(stats.toast_bytes),
            _mb(stats.live_payload_bytes),
            _mb(stats.wasted_bytes),
        )
        return ToastReclaimOutcome.SKIPPED

    logger.info(
        "execution_history TOAST is bloated (%s file, %s live, %s wasted); running VACUUM FULL",
        _mb(stats.toast_bytes),
        _mb(stats.live_payload_bytes),
        _mb(stats.wasted_bytes),
    )
    started = time.monotonic()
    try:
        await _vacuum_full_execution_history()
    except asyncpg.exceptions.LockNotAvailableError:
        logger.warning(
            "execution_history was busy for %d ms; skipping TOAST reclaim until next week",
            REWRITE_LOCK_TIMEOUT_MS,
        )
        return ToastReclaimOutcome.BUSY
    elapsed = time.monotonic() - started

    async with async_session_maker() as db:
        after = await read_toast_bloat_stats(db)
    logger.info(
        "execution_history TOAST rewritten in %.1fs: %s -> %s",
        elapsed,
        _mb(stats.toast_bytes),
        _mb(after.toast_bytes) if after is not None else "unknown",
    )
    return ToastReclaimOutcome.REWRITTEN


async def _vacuum_full_execution_history() -> None:
    # A dedicated connection, not a pooled one: VACUUM cannot run inside a
    # transaction block, and the timeouts must not leak into the pool.
    connection = await asyncpg.connect(
        libpq_dsn(),
        server_settings={
            **listener_server_settings(),
            "lock_timeout": str(REWRITE_LOCK_TIMEOUT_MS),
            "statement_timeout": str(REWRITE_STATEMENT_TIMEOUT_MS),
        },
    )
    try:
        await connection.execute("VACUUM (FULL) execution_history")
    finally:
        await connection.close()


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.0f} MB"
