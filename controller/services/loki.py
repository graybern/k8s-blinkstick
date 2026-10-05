import logging

import httpx

from controller.config import LOKI_URL, PROMETHEUS_TIMEOUT

log = logging.getLogger(__name__)


class LokiClient:
    def __init__(self):
        self._client = httpx.AsyncClient(
            base_url=LOKI_URL,
            timeout=PROMETHEUS_TIMEOUT,
        )

    async def query(self, logql: str, limit: int = 100) -> list[dict]:
        try:
            resp = await self._client.get(
                "/loki/api/v1/query",
                params={"query": logql, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "success":
                log.warning("Loki query returned status=%s", data.get("status"))
                return []
            return data.get("data", {}).get("result", [])
        except httpx.TimeoutException:
            log.warning("Loki query timed out: %s", logql[:80])
            return []
        except httpx.HTTPStatusError as e:
            log.warning("Loki HTTP error: %s", e)
            return []
        except Exception:
            log.exception("Loki query failed: %s", logql[:80])
            return []

    async def query_range(self, logql: str, start: str, end: str, limit: int = 100) -> list[dict]:
        try:
            resp = await self._client.get(
                "/loki/api/v1/query_range",
                params={"query": logql, "start": start, "end": end, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "success":
                log.warning("Loki query_range returned status=%s", data.get("status"))
                return []
            return data.get("data", {}).get("result", [])
        except httpx.TimeoutException:
            log.warning("Loki query_range timed out: %s", logql[:80])
            return []
        except httpx.HTTPStatusError as e:
            log.warning("Loki HTTP error: %s", e)
            return []
        except Exception:
            log.exception("Loki query_range failed: %s", logql[:80])
            return []

    async def close(self):
        await self._client.aclose()
