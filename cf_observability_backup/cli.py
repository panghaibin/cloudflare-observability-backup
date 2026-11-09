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
from dotenv import dotenv_values

from .archive import sync
from .cloudflare_events import QueryError, event_pages
from .config import Settings, load_settings


def single_worker_settings(args: argparse.Namespace) -> Settings:
    config = dotenv_values(".env", encoding="utf-8-sig", interpolate=False)
    account = args.account_id or os.getenv("CLOUDFLARE_ACCOUNT_ID", config.get("CLOUDFLARE_ACCOUNT_ID"))
    token = os.getenv("CLOUDFLARE_API_TOKEN", config.get("CLOUDFLARE_API_TOKEN"))
    initial_hours = 24 if args.initial_hours is None else args.initial_hours
    if not args.worker or not args.worker.strip() or args.db is None:
        raise ValueError("pass --config or provide both --worker and --db")
    if not token:
        raise ValueError("set CLOUDFLARE_API_TOKEN in the environment or .env")
    if not account:
        raise ValueError("set CLOUDFLARE_ACCOUNT_ID in the environment or .env, or pass --account-id")
    if initial_hours < 1:
        raise ValueError("--initial-hours must be at least 1")
    return Settings(account, token, (args.worker,), args.db, initial_hours)


def run(settings: Settings) -> int:
    until_ms = time.time_ns() // 1_000_000
    until = datetime.fromtimestamp(until_ms / 1000, timezone.utc).isoformat()
    exit_code = 0
    with (
        Cloudflare(api_token=settings.api_token, timeout=60.0) as client,
        closing(sqlite3.connect(settings.db, timeout=30.0)) as db,
    ):
        for worker in settings.workers:
            try:
                inserted = sync(
                    db,
                    settings.account_id,
                    worker,
                    until_ms,
                    settings.initial_hours,
                    partial(event_pages, client, settings.account_id, worker),
                )
            except (APIError, OSError, QueryError, ValueError, sqlite3.Error) as exc:
                print(f"Sync failed for {worker}: {exc}", file=sys.stderr)
                exit_code = 1
                continue
            print(f"{worker}: sync complete at cutoff {until}, {inserted} new events")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Incrementally archive Cloudflare Workers Observability events."
    )
    parser.add_argument("--config", type=Path, help="TOML configuration file")
    parser.add_argument("--account-id")
    parser.add_argument("--worker", help="Deployed Worker script name")
    parser.add_argument("--db", type=Path, help="SQLite archive path")
    parser.add_argument("--initial-hours", type=int, help="First sync lookback (default: 24)")
    args = parser.parse_args()
    if args.config and any(
        option is not None for option in (args.account_id, args.worker, args.db, args.initial_hours)
    ):
        parser.error("--config cannot be combined with single-Worker options")
    try:
        settings = load_settings(args.config) if args.config else single_worker_settings(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    try:
        return run(settings)
    except (APIError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"Unable to start sync: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
