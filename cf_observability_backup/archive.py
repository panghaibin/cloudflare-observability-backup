import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timezone

HOUR_MS = 3_600_000
WINDOW_MS = 30 * 60_000
OVERLAP_MS = 30 * 60_000

logger = logging.getLogger(__name__)

PROGRESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_progress (
    account_id TEXT NOT NULL,
    worker TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    through_ms INTEGER NOT NULL,
    PRIMARY KEY (account_id, worker)
)
"""
TABLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_tables (
    account_id TEXT NOT NULL,
    worker TEXT NOT NULL,
    table_name TEXT NOT NULL UNIQUE,
    PRIMARY KEY (account_id, worker)
)
"""


def utc_timestamp(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat()


def ensure_worker_table(db: sqlite3.Connection, account_id: str, worker: str) -> str:
    label = re.sub(r"[^a-z0-9]+", "_", worker.lower()).strip("_")[:48] or "worker"
    source_key = json.dumps([account_id, worker]).encode("ascii")
    suffix = hashlib.sha256(source_key).hexdigest()[:16]
    table = f"events_{label}_{suffix}"
    db.execute(
        """
        INSERT INTO worker_tables (account_id, worker, table_name) VALUES (?, ?, ?)
        ON CONFLICT (account_id, worker) DO NOTHING
        """,
        (account_id, worker, table),
    )
    db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            event_id TEXT NOT NULL PRIMARY KEY,
            timestamp_ms INTEGER NOT NULL,
            event_json TEXT NOT NULL
        )
        """
    )
    db.execute(f"CREATE INDEX IF NOT EXISTS {table}_by_time ON {table} (timestamp_ms)")
    return table


def event_rows(events: Iterable[dict]) -> Iterable[tuple[str, int, str]]:
    for event in events:
        try:
            event_id = event["$metadata"]["id"]
            timestamp = event["timestamp"]
        except (KeyError, TypeError) as exc:
            raise ValueError("Cloudflare event is missing its ID or timestamp") from exc
        if not isinstance(event_id, str) or not event_id or type(timestamp) is not int:
            raise ValueError("Cloudflare event has an invalid ID or timestamp")
        yield event_id, timestamp, json.dumps(
            event, separators=(",", ":"), allow_nan=False
        )


def sync(
        db: sqlite3.Connection,
        account_id: str,
        worker: str,
        until_ms: int,
        initial_hours: int,
        fetch_pages: Callable[[int, int], Iterable[list[dict]]],
) -> int:
    db.execute("PRAGMA journal_mode = WAL")
    source = (account_id, worker)
    initial_ms = max(0, until_ms - initial_hours * HOUR_MS)
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(PROGRESS_SCHEMA)
        db.execute(TABLES_SCHEMA)
        table = ensure_worker_table(db, account_id, worker)
        db.execute(
            """
            INSERT INTO sync_progress VALUES (?, ?, ?, ?)
            ON CONFLICT (account_id, worker) DO NOTHING
            """,
            (*source, initial_ms, initial_ms),
        )
        start_ms, through_ms = db.execute(
            "SELECT start_ms, through_ms FROM sync_progress WHERE account_id = ? AND worker = ?",
            source,
        ).fetchone()

    logger.info("worker=%s table=%s checkpoint=%s", worker, table, utc_timestamp(through_ms))
    if until_ms <= through_ms:
        return 0

    inserted = 0
    window_start = max(start_ms, through_ms - OVERLAP_MS)
    while window_start < until_ms:
        window_end = min(window_start + WINDOW_MS, until_ms)
        logger.info(
            "worker=%s querying from=%s to=%s",
            worker, utc_timestamp(window_start), utc_timestamp(window_end),
        )
        pages = fetched = window_inserted = 0
        for events in fetch_pages(window_start, window_end):
            with db:
                cursor = db.executemany(
                    f"""
                    INSERT INTO {table} (event_id, timestamp_ms, event_json) VALUES (?, ?, ?)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    event_rows(events),
                )
            pages += 1
            fetched += len(events)
            window_inserted += cursor.rowcount
        with db:
            db.execute(
                """
                UPDATE sync_progress SET through_ms = max(through_ms, ?)
                WHERE account_id = ? AND worker = ?
                """,
                (window_end, *source),
            )
        logger.info(
            "worker=%s window complete pages=%d fetched=%d inserted=%d duplicates=%d",
            worker, pages, fetched, window_inserted, fetched - window_inserted,
        )
        inserted += window_inserted
        window_start = window_end
    return inserted
