import asyncio
import logging

from controller.config import ALERTMANAGER_POLL_INTERVAL, OVERLAY_ALERT_ENABLED

log = logging.getLogger(__name__)


class AlertOverlay:
    name = "alert-escalation"

    def __init__(self, engine, alertmanager):
        self._engine = engine
        self._alertmanager = alertmanager
        self._poll_task: asyncio.Task | None = None

    async def start(self):
        if not OVERLAY_ALERT_ENABLED:
            log.info("Alert overlay disabled")
            return
        if not self._alertmanager:
            log.warning("Alert overlay enabled but no AlertManager client")
            return
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("Alert overlay started (poll every %ds)", ALERTMANAGER_POLL_INTERVAL)

    async def stop(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        log.info("Alert overlay stopped")

    async def _poll_loop(self):
        while True:
            try:
                critical = await self._alertmanager.get_firing_critical()
                if critical:
                    first = critical[0]
                    name = first.get("labels", {}).get("alertname", "unknown")
                    n = len(critical)
                    reason = f"CRITICAL: {name}" if n == 1 else f"CRITICAL: {name} (+{n - 1} more)"
                    self._engine.trigger_overlay(
                        "alert-escalation",
                        reason=reason,
                        priority=3,
                        duration=float(ALERTMANAGER_POLL_INTERVAL),
                        color=(255, 0, 0),
                        effect="blink",
                        params={"delay": 0.25},
                    )
                await asyncio.sleep(ALERTMANAGER_POLL_INTERVAL)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Alert overlay poll failed")
                await asyncio.sleep(ALERTMANAGER_POLL_INTERVAL)
