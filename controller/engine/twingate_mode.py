import asyncio
import json
import logging
import time

from controller.config import LOKI_POLL_INTERVAL, OVERLAY_TWINGATE_ENABLED, TWINGATE_LOGQL_QUERY

log = logging.getLogger(__name__)

DEFAULT_LOGQL = '{namespace="twingate", container="connector"} |= "established_connection"'


class TwingateOverlay:
    name = "twingate-flash"

    def __init__(self, engine, loki):
        self._engine = engine
        self._loki = loki
        self._poll_task: asyncio.Task | None = None
        self._last_seen_ts: float = 0

    async def start(self):
        if not OVERLAY_TWINGATE_ENABLED:
            log.info("Twingate overlay disabled")
            return
        if not self._loki:
            log.warning("Twingate overlay enabled but no Loki client")
            return
        self._last_seen_ts = time.time() * 1000
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("Twingate overlay started (poll every %ds)", LOKI_POLL_INTERVAL)

    async def stop(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        log.info("Twingate overlay stopped")

    async def _poll_loop(self):
        logql = TWINGATE_LOGQL_QUERY or DEFAULT_LOGQL
        while True:
            try:
                results = await self._loki.query(logql, limit=50)
                self._process_results(results)
                await asyncio.sleep(LOKI_POLL_INTERVAL)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Twingate overlay poll failed")
                await asyncio.sleep(LOKI_POLL_INTERVAL)

    def _process_results(self, results: list[dict]):
        max_ts = self._last_seen_ts
        for stream in results:
            for entry in stream.get("values", []):
                line = entry[1] if len(entry) > 1 else ""
                if not line.startswith("ANALYTICS "):
                    continue
                try:
                    data = json.loads(line[len("ANALYTICS "):])
                except (json.JSONDecodeError, TypeError):
                    continue
                if data.get("event_type") != "established_connection":
                    continue
                ts = data.get("timestamp", 0)
                if not isinstance(ts, (int, float)) or ts <= self._last_seen_ts:
                    continue
                max_ts = max(max_ts, ts)
                user = data.get("user", {}).get("email", "unknown")
                resource = data.get("resource", {}).get("address", "unknown")
                self._engine.trigger_overlay(
                    "twingate-flash",
                    reason=f"{user} → {resource}",
                    priority=1,
                    duration=3.0,
                    color=(0, 255, 255),
                    effect="blink",
                    params={"delay": 0.2},
                )
        self._last_seen_ts = max_ts
