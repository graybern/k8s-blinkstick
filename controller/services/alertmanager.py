import logging

import httpx

from controller.config import ALERTMANAGER_URL, PROMETHEUS_TIMEOUT

log = logging.getLogger(__name__)


class AlertManagerClient:
    def __init__(self):
        self._client = httpx.AsyncClient(
            base_url=ALERTMANAGER_URL,
            timeout=PROMETHEUS_TIMEOUT,
        )

    async def get_alerts(self, silenced: bool = False, inhibited: bool = False) -> list[dict]:
        try:
            resp = await self._client.get(
                "/api/v2/alerts",
                params={
                    "silenced": str(silenced).lower(),
                    "inhibited": str(inhibited).lower(),
                },
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            log.warning("AlertManager request timed out")
            return []
        except httpx.HTTPStatusError as e:
            log.warning("AlertManager HTTP error: %s", e)
            return []
        except Exception:
            log.exception("AlertManager request failed")
            return []

    async def get_firing_critical(self) -> list[dict]:
        alerts = await self.get_alerts()
        return [
            a for a in alerts
            if a.get("status", {}).get("state") == "active"
            and a.get("labels", {}).get("severity") == "critical"
        ]

    async def close(self):
        await self._client.aclose()
