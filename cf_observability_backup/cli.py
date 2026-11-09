import argparse
import logging
import sqlite3
import sys
import time
from contextlib import closing
from functools import partial
from pathlib import Path

from cloudflare import APIError, Cloudflare

from .archive import sync, utc_timestamp
from .cloudflare_events import QueryError, event_pages
from .config import Settings, load_settings
from .run_logging import run_logging

logger = logging.getLogger(__name__)


def run(settings: Settings) -> int:
    started = time.monotonic()
    until_ms = time.time_ns() // 1_000_000
    logger.info(
        "Sync started db=%s workers=%d cutoff=%s",
        settings.db, len(settings.workers), utc_timestamp(until_ms),
    )
    failures = total_inserted = 0
    with (
        Cloudflare(api_token=settings.api_token, timeout=60.0) as client,
        closing(sqlite3.connect(settings.db, timeout=30.0)) as db,
    ):
        for worker in settings.workers:
            worker_started = time.monotonic()
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
                logger.error(
                    "worker=%s failed after %.2fs: %s",
                    worker, time.monotonic() - worker_started, exc,
                )
                failures += 1
                continue
            total_inserted += inserted
            logger.info(
                "worker=%s sync complete inserted=%d elapsed=%.2fs",
                worker, inserted, time.monotonic() - worker_started,
            )
    logger.info(
        "Sync finished succeeded=%d failed=%d inserted_for_succeeded=%d elapsed=%.2fs",
        len(settings.workers) - failures, failures, total_inserted, time.monotonic() - started,
    )
    return int(failures > 0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Incrementally archive Cloudflare Workers Observability events."
    )
    parser.add_argument(
        "--config", type=Path, default=Path("backup.toml"),
        help="TOML configuration file (default: backup.toml)",
    )
    args = parser.parse_args()
    try:
        with run_logging(args.config.absolute().parent):
            try:
                settings = load_settings(args.config)
            except (OSError, ValueError) as exc:
                logger.error("Unable to load configuration %s: %s", args.config, exc)
                return 2

            try:
                return run(settings)
            except (APIError, OSError, ValueError, sqlite3.Error) as exc:
                logger.error("Unable to start sync: %s", exc)
                return 1
    except OSError as exc:
        print(f"Unable to open run log: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
