import asyncio
import logging

from controller.config import STATUS_POLL_INTERVAL
from controller.services.mqtt_client import MQTTClient
from controller.services.prometheus import PrometheusClient

log = logging.getLogger(__name__)

SEVERITY_COLORS = {
    "critical": ((255, 0, 0), "blink", {"delay": 500, "repeats": 0}),
    "warning": ((255, 140, 0), "solid", {}),
    "offline": ((0, 0, 60), "solid", {}),
    "healthy": ((0, 255, 0), "pulse", {"duration": 3000, "steps": 50, "repeats": 0}),
    "unknown": ((0, 0, 0), "off", {}),
}


class StatusMode:
    name = "status"
    layer = "background"
    led_strategy = "unified"

    def __init__(self, prometheus: PrometheusClient, mqtt_client: MQTTClient):
        self._prometheus = prometheus
        self._mqtt_client = mqtt_client
        self._node_health: dict[str, dict] = {}
        self._poll_task: asyncio.Task | None = None

    async def start(self):
        try:
            await self._prometheus.discover_instance_mapping()
        except Exception:
            log.exception("Instance mapping discovery failed, using fallback")
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("Status mode started (poll every %ds)", STATUS_POLL_INTERVAL)

    async def stop(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        log.info("Status mode stopped")

    async def tick(self) -> dict[str, list[dict]] | None:
        active_nodes = self._mqtt_client.get_active_nodes()
        if not active_nodes or not self._node_health:
            return None

        result = {}
        for node in active_nodes:
            led_count = self._mqtt_client.get_node_led_count(node)
            if led_count == 0:
                continue
            health = self._node_health.get(node)
            severity, color, effect, params = self._compute_state(node, health)
            r, g, b = color
            leds = [{"index": i, "r": r, "g": g, "b": b} for i in range(led_count)]
            result[node] = {
                "leds": leds,
                "effect": effect,
                "params": params,
                "_severity": severity,
            }
        return result if result else None

    def get_node_health(self) -> dict[str, dict]:
        return dict(self._node_health)

    def _compute_state(self, node: str, health: dict | None):
        if health is None:
            color, effect, params = SEVERITY_COLORS["unknown"]
            return "unknown", color, effect, params

        up = health.get("up", False)
        k8s_ready = health.get("k8s_ready", False)
        cpu = health.get("cpu")
        memory = health.get("memory")
        disk = health.get("disk")

        if cpu is None or memory is None or disk is None:
            color, effect, params = SEVERITY_COLORS["critical"]
            return "critical", color, effect, params

        if not up or not k8s_ready:
            color, effect, params = SEVERITY_COLORS["critical"]
            return "critical", color, effect, params

        if cpu > 0.9 or memory > 0.9 or disk > 0.9:
            color, effect, params = SEVERITY_COLORS["critical"]
            return "critical", color, effect, params

        if cpu > 0.7 or memory > 0.7 or disk > 0.8:
            color, effect, params = SEVERITY_COLORS["warning"]
            return "warning", color, effect, params

        if not self._mqtt_client.is_agent_online(node):
            color, effect, params = SEVERITY_COLORS["offline"]
            return "offline", color, effect, params

        color, effect, params = SEVERITY_COLORS["healthy"]
        return "healthy", color, effect, params

    async def _poll_loop(self):
        while True:
            try:
                health_data = await self._prometheus.query_node_health()
                if health_data:
                    self._node_health = health_data
                    log.debug("Prometheus poll: %d nodes", len(health_data))
                await asyncio.sleep(STATUS_POLL_INTERVAL)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Prometheus poll failed, keeping last known state")
                await asyncio.sleep(STATUS_POLL_INTERVAL)
