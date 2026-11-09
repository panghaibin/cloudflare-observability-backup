import json
import sqlite3
import unittest
from contextlib import closing
from functools import partial

import httpx
from cloudflare import Cloudflare

from cf_observability_backup.archive import HOUR_MS, OVERLAP_MS, sync
from cf_observability_backup.cloudflare_events import QueryError, event_pages


class SyncTest(unittest.TestCase):
    def test_retry_resumes_with_correct_sdk_payload_and_deduplicates_pages(self):
        cutoff = 100 * HOUR_MS
        first = {
            "$metadata": {"id": "one"},
            "timestamp": cutoff - 2,
            "source": {"arbitrary": ["a", 1]},
            "unknownField": {"preserved": True},
        }
        second = {
            "$metadata": {"id": "two"},
            "timestamp": cutoff - 1,
            "source": '{"old":"string"}',
        }
        calls = []
        fail_second_page = True

        def handle(request):
            body = json.loads(request.content)
            calls.append(body)
            cursor = body.get("offset")
            events = {None: [first], "one": [second], "two": []}[cursor]
            sampled = cursor == "one" and fail_second_page
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {
                        "statistics": {"abr_level": 2 if sampled else 1},
                        "events": {"events": events},
                    },
                },
            )

        with (
            Cloudflare(
                api_token="fake",
                http_client=httpx.Client(transport=httpx.MockTransport(handle)),
            ) as client,
            closing(sqlite3.connect(":memory:")) as db,
        ):
            fetch = partial(event_pages, client, "test-account", "test-worker")
            with self.assertRaisesRegex(QueryError, "sampled"):
                sync(db, "test-account", "test-worker", cutoff, 1, fetch)
            self.assertEqual(
                db.execute("SELECT through_ms FROM sync_progress").fetchone(),
                (cutoff - HOUR_MS,),
            )

            fail_second_page = False
            calls.clear()
            self.assertEqual(sync(db, "test-account", "test-worker", cutoff, 1, fetch), 1)
            self.assertEqual([call.get("offset") for call in calls], [None, "one", "two"])
            for call in calls:
                self.assertEqual(call["timeframe"], {"from": cutoff - HOUR_MS, "to": cutoff})
                self.assertEqual(call["parameters"]["filters"][0]["value"], "test-worker")
            self.assertEqual(calls[1]["offsetDirection"], "next")
            saved = db.execute("SELECT event_json FROM events ORDER BY event_id")
            self.assertEqual([json.loads(row[0]) for row in saved], [first, second])

            calls.clear()
            later = cutoff + 600_000
            self.assertEqual(sync(db, "test-account", "test-worker", later, 1, fetch), 0)
            self.assertEqual(calls[0]["timeframe"], {"from": cutoff - OVERLAP_MS, "to": later})
            self.assertEqual(
                db.execute("SELECT through_ms FROM sync_progress").fetchone(), (later,)
            )
