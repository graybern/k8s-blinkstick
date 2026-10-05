import asyncio
import logging

from controller.config import STATUS_POLL_INTERVAL
from controller.services.mqtt_client import MQTTClient
from controller.services.prometheus import PrometheusClient

log = logging.getLogger(__name__)

COLOR_STOPS = [
    (30, (0, 0, 255)),
    (50, (0, 255, 0)),
    (65, (255, 255, 0)),
    (80, (255, 0, 0)),
]


def _temp_to_color(temp_c: float) -> tuple[int, int, int]:
    if temp_c <= COLOR_STOPS[0][0]:
        return COLOR_STOPS[0][1]
    if temp_c >= COLOR_STOPS[-1][0]:
        return COLOR_STOPS[-1][1]
    for i in range(len(COLOR_STOPS) - 1):
        t0, c0 = COLOR_STOPS[i]
        t1, c1 = COLOR_STOPS[i + 1]
        if t0 <= temp_c <= t1:
            frac = (temp_c - t0) / (t1 - t0)
            r = int(c0[0] + (c1[0] - c0[0]) * frac)
            g = int(c0[1] + (c1[1] - c0[1]) * frac)
            b = int(c0[2] + (c1[2] - c0[2]) * frac)
            return (r, g, b)
    return COLOR_STOPS[-1][1]


class TemperatureMode:
    name = "temperature"
    layer = "background"
    led_strategy = "unified"

    def __init__(self, prometheus: PrometheusClient, mqtt_client: MQTTClient):
        self._prometheus = prometheus
        self._mqtt = mqtt_client
        self._temps: dict[str, float] = {}
        self._poll_task: asyncio.Task | None = None

    async def start(self):
        try:
            await self._prometheus.discover_instance_mapping()
        except Exception:
            log.exception("Instance mapping discovery failed, using fallback")
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("Temperature mode started (poll every %ds)", STATUS_POLL_INTERVAL)

    async def stop(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        log.info("Temperature mode stopped")

    async def tick(self):
        active_nodes = self._mqtt.get_active_nodes()
        if not active_nodes:
            return None

        result = {}
        for node in active_nodes:
            led_count = self._mqtt.get_node_led_count(node)
            if led_count == 0:
                continue
            temp = self._temps.get(node, 45.0)
            r, g, b = _temp_to_color(temp)
            leds = [{"index": i, "r": r, "g": g, "b": b} for i in range(led_count)]
            result[node] = {"leds": leds, "effect": "solid", "params": {}}

        return result if result else None

    async def _poll_loop(self):
        while True:
            try:
                temps = await self._prometheus.query_cpu_temperature()
                if temps:
                    self._temps = temps
                    log.debug("Temperature poll: %s", {n: f"{t:.1f}°C" for n, t in temps.items()})
                await asyncio.sleep(STATUS_POLL_INTERVAL)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Temperature poll failed, keeping last known temps")
                await asyncio.sleep(STATUS_POLL_INTERVAL)
