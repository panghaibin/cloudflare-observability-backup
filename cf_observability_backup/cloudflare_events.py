from collections.abc import Iterator

from cloudflare import Cloudflare


class QueryError(RuntimeError):
    pass


def event_pages(
    client: Cloudflare, account_id: str, worker: str, start_ms: int, end_ms: int
) -> Iterator[list[dict]]:
    query = client.workers.observability.telemetry.with_raw_response.query
    cursor = {}
    seen_cursors = set()
    while True:
        response = query(
            account_id=account_id,
            query_id="cf-observability-backup",
            timeframe={"from": start_ms, "to": end_ms},
            view="events",
            dry=True,
            limit=2000,
            parameters={
                "datasets": ["cloudflare-workers"],
                "filters": [
                    {
                        "key": "$workers.scriptName",
                        "operation": "eq",
                        "type": "string",
                        "value": worker,
                    }
                ],
            },
            **cursor,
        ).json()
        try:
            if response["success"] is not True:
                raise QueryError("Cloudflare reported an unsuccessful query")
            result = response["result"]
            statistics = result.get("statistics") or {}
            run_statistics = (result.get("run") or {}).get("statistics") or {}
            if any(
                stats.get("abr_level") not in (None, 1)
                for stats in (statistics, run_statistics)
            ):
                raise QueryError("Cloudflare sampled the query results")
            event_result = result["events"]
            events = event_result.get("events")
            if events is None and event_result.get("count") == 0:
                events = []
            if not isinstance(events, list):
                raise QueryError("Cloudflare did not return an event list")
            if not events:
                return
            last_id = events[-1]["$metadata"]["id"]
            if not isinstance(last_id, str) or not last_id:
                raise QueryError("Cloudflare returned an invalid pagination cursor")
        except (AttributeError, KeyError, TypeError) as exc:
            raise QueryError("Unexpected Cloudflare query response") from exc

        if last_id in seen_cursors:
            raise QueryError("Cloudflare repeated a pagination cursor")
        seen_cursors.add(last_id)
        yield events
        cursor = {"offset": last_id, "offset_direction": "next"}
