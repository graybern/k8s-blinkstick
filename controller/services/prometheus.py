import asyncio
import logging

import httpx

from controller.config import (
    PROMETHEUS_URL,
    PROMETHEUS_TIMEOUT,
    FALLBACK_INSTANCE_MAP,
)

log = logging.getLogger(__name__)

QUERIES = {
    "up": 'up{job="node-exporter"}',
    "cpu": '1 - avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[2m]))',
    "memory": "1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)",
    "disk": '1 - (node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})',
    "k8s_ready": 'kube_node_status_condition{condition="Ready",status="true"}',
}


class PrometheusClient:
    def __init__(self):
        self._client = httpx.AsyncClient(
            base_url=PROMETHEUS_URL,
            timeout=PROMETHEUS_TIMEOUT,
        )
        self._instance_to_node: dict[str, str] = {}

    async def discover_instance_mapping(self):
        try:
            results = await self._query('kube_node_info')
            mapping = {}
            for r in results:
                node = r["metric"].get("node", "")
                instance = r["metric"].get("internal_ip", "")
                if node and instance:
                    mapping[f"{instance}:9100"] = node
            if mapping:
                self._instance_to_node = mapping
                log.info("Discovered instance→node mapping: %s", mapping)
                return
        except Exception:
            log.warning("Dynamic instance mapping failed, trying node label")

        try:
            results = await self._query('up{job="node-exporter"}')
            mapping = {}
            for r in results:
                instance = r["metric"].get("instance", "")
                node = r["metric"].get("node", "")
                if instance and node:
                    mapping[instance] = node
            if mapping:
                self._instance_to_node = mapping
                log.info("Discovered instance→node mapping from node label: %s", mapping)
                return
        except Exception:
            log.warning("Node label discovery also failed")

        self._instance_to_node = dict(FALLBACK_INSTANCE_MAP)
        log.info("Using fallback instance→node mapping: %s", self._instance_to_node)

    def resolve_node(self, instance: str) -> str | None:
        return self._instance_to_node.get(instance)

    async def query_node_health(self) -> dict[str, dict]:
        tasks = {
            name: self._query(promql)
            for name, promql in QUERIES.items()
        }
        results = {}
        gathered = await asyncio.gather(
            *tasks.values(), return_exceptions=True,
        )
        for name, result in zip(tasks.keys(), gathered):
            if isinstance(result, Exception):
                log.warning("Prometheus query '%s' failed: %s", name, result)
                results[name] = []
            else:
                results[name] = result

        nodes: dict[str, dict] = {}

        for r in results.get("up", []):
            instance = r["metric"].get("instance", "")
            node = self.resolve_node(instance)
            if not node:
                continue
            val = float(r["value"][1]) if r.get("value") else 0
            nodes.setdefault(node, {})["up"] = val >= 1.0

        for r in results.get("cpu", []):
            instance = r["metric"].get("instance", "")
            node = self.resolve_node(instance)
            if not node:
                continue
            nodes.setdefault(node, {})["cpu"] = float(r["value"][1])

        for r in results.get("memory", []):
            instance = r["metric"].get("instance", "")
            node = self.resolve_node(instance)
            if not node:
                continue
            nodes.setdefault(node, {})["memory"] = float(r["value"][1])

        for r in results.get("disk", []):
            instance = r["metric"].get("instance", "")
            node = self.resolve_node(instance)
            if not node:
                continue
            nodes.setdefault(node, {})["disk"] = float(r["value"][1])

        for r in results.get("k8s_ready", []):
            node = r["metric"].get("node", "")
            if not node:
                continue
            val = float(r["value"][1]) if r.get("value") else 0
            nodes.setdefault(node, {})["k8s_ready"] = val >= 1.0

        return nodes

    async def _query(self, promql: str) -> list[dict]:
        try:
            resp = await self._client.get(
                "/api/v1/query",
                params={"query": promql},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "success":
                log.warning("Prometheus query returned status=%s", data.get("status"))
                return []
            return data.get("data", {}).get("result", [])
        except httpx.TimeoutException:
            log.warning("Prometheus query timed out: %s", promql[:60])
            return []
        except httpx.HTTPStatusError as e:
            log.warning("Prometheus HTTP error: %s", e)
            return []
        except Exception:
            log.exception("Prometheus query failed: %s", promql[:60])
            return []

    async def close(self):
        await self._client.aclose()
