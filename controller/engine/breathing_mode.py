import asyncio
import logging
import math
import time

from controller.config import STATUS_POLL_INTERVAL
from controller.services.mqtt_client import MQTTClient
from controller.services.prometheus import PrometheusClient

log = logging.getLogger(__name__)

MIN_PERIOD = 0.5
MAX_PERIOD = 4.0


class BreathingMode:
    name = "breathing"
    layer = "background"
    led_strategy = "unified"

    def __init__(self, prometheus: PrometheusClient, mqtt_client: MQTTClient):
        self._prometheus = prometheus
        self._mqtt = mqtt_client
        self._cpu: dict[str, float] = {}
        self._poll_task: asyncio.Task | None = None

    async def start(self):
        try:
            await self._prometheus.discover_instance_mapping()
        except Exception:
            log.exception("Instance mapping discovery failed, using fallback")
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("Breathing mode started (poll every %ds)", STATUS_POLL_INTERVAL)

    async def stop(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        log.info("Breathing mode stopped")

    async def tick(self):
        active_nodes = self._mqtt.get_active_nodes()
        if not active_nodes:
            return None

        now = time.monotonic()
        result = {}
        for node in active_nodes:
            led_count = self._mqtt.get_node_led_count(node)
            if led_count == 0:
                continue
            cpu = self._cpu.get(node, 0.05)
            period = MAX_PERIOD - (MAX_PERIOD - MIN_PERIOD) * cpu
            brightness = (math.sin(now * 2 * math.pi / period) + 1) / 2
            brightness = 0.05 + brightness * 0.95
            if cpu < 0.5:
                r, g, b = 0, int(255 * brightness), 0
            else:
                intensity = int(255 * brightness)
                r, g, b = intensity, int(intensity * 0.6), 0
            leds = [{"index": i, "r": r, "g": g, "b": b} for i in range(led_count)]
            result[node] = {"leds": leds, "effect": "solid", "params": {}}

        return result if result else None

    async def _poll_loop(self):
        while True:
            try:
                health = await self._prometheus.query_node_health()
                if health:
                    self._cpu = {node: data.get("cpu", 0.05) for node, data in health.items()}
                    log.debug("Breathing poll: %d nodes", len(self._cpu))
                await asyncio.sleep(STATUS_POLL_INTERVAL)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Breathing poll failed, keeping last known CPU")
                await asyncio.sleep(STATUS_POLL_INTERVAL)
