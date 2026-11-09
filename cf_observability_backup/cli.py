import argparse
import os
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from cloudflare import APIError, Cloudflare

from .archive import sync
from .cloudflare_events import QueryError, event_pages


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Incrementally archive one Cloudflare Worker's Observability events."
    )
    parser.add_argument("--account-id", default=os.getenv("CLOUDFLARE_ACCOUNT_ID"))
    parser.add_argument("--worker", required=True, help="Deployed Worker script name")
    parser.add_argument("--db", type=Path, required=True, help="SQLite archive path")
    parser.add_argument(
        "--initial-hours", type=int, default=24, help="First sync lookback (default: 24)"
    )
    args = parser.parse_args()
    token = os.getenv("CLOUDFLARE_API_TOKEN")
    if not token:
        parser.error("set CLOUDFLARE_API_TOKEN")
    if not args.account_id:
        parser.error("set CLOUDFLARE_ACCOUNT_ID or pass --account-id")
    if args.initial_hours < 1:
        parser.error("--initial-hours must be at least 1")
    if not args.worker.strip():
        parser.error("--worker must be a non-empty script name")

    until_ms = time.time_ns() // 1_000_000
    try:
        with (
            Cloudflare(api_token=token, timeout=60.0) as client,
            closing(sqlite3.connect(args.db, timeout=30.0)) as db,
        ):
            inserted = sync(
                db,
                args.account_id,
                args.worker,
                until_ms,
                args.initial_hours,
                partial(event_pages, client, args.account_id, args.worker),
            )
    except (APIError, OSError, QueryError, ValueError, sqlite3.Error) as exc:
        print(f"Sync failed for {args.worker}: {exc}", file=sys.stderr)
        return 1

    until = datetime.fromtimestamp(until_ms / 1000, timezone.utc).isoformat()
    print(f"{args.worker}: sync complete at cutoff {until}, {inserted} new events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
