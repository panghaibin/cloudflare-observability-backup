import json
import sqlite3
from collections.abc import Callable, Iterable


HOUR_MS = 3_600_000
OVERLAP_MS = 30 * 60_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    account_id TEXT NOT NULL,
    worker TEXT NOT NULL,
    event_id TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (account_id, worker, event_id)
);
CREATE INDEX IF NOT EXISTS events_by_time
    ON events (account_id, worker, timestamp_ms);
CREATE TABLE IF NOT EXISTS sync_progress (
    account_id TEXT NOT NULL,
    worker TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    through_ms INTEGER NOT NULL,
    PRIMARY KEY (account_id, worker)
);
"""


def event_rows(
    account_id: str, worker: str, events: Iterable[dict]
) -> Iterable[tuple[str, str, str, int, str]]:
    for event in events:
        try:
            event_id = event["$metadata"]["id"]
            timestamp = event["timestamp"]
        except (KeyError, TypeError) as exc:
            raise ValueError("Cloudflare event is missing its ID or timestamp") from exc
        if not isinstance(event_id, str) or not event_id or type(timestamp) is not int:
            raise ValueError("Cloudflare event has an invalid ID or timestamp")
        yield account_id, worker, event_id, timestamp, json.dumps(
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
    db.executescript(SCHEMA)
    source = (account_id, worker)
    initial_ms = max(0, until_ms - initial_hours * HOUR_MS)
    with db:
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

    if until_ms <= through_ms:
        return 0

    inserted = 0
    window_start = max(start_ms, through_ms - OVERLAP_MS)
    while window_start < until_ms:
        window_end = min(window_start + HOUR_MS, until_ms)
        for events in fetch_pages(window_start, window_end):
            with db:
                cursor = db.executemany(
                    """
                    INSERT INTO events VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (account_id, worker, event_id) DO NOTHING
                    """,
                    event_rows(account_id, worker, events),
                )
            inserted += cursor.rowcount
        with db:
            db.execute(
                """
                UPDATE sync_progress SET through_ms = max(through_ms, ?)
                WHERE account_id = ? AND worker = ?
                """,
                (window_end, *source),
            )
        window_start = window_end
    return inserted
