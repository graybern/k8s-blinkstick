import asyncio
import logging
import math

from controller.config import STATUS_POLL_INTERVAL
from controller.services.mqtt_client import MQTTClient
from controller.services.prometheus import PrometheusClient

log = logging.getLogger(__name__)

MAX_BPS = 10_000_000
MIN_BRIGHTNESS = 10


def _bps_to_brightness(bps: float) -> int:
    if bps <= 0:
        return MIN_BRIGHTNESS
    normalized = min(bps / MAX_BPS, 1.0)
    return MIN_BRIGHTNESS + int((255 - MIN_BRIGHTNESS) * (math.log1p(normalized * 99) / math.log1p(99)))


class NetworkMode:
    name = "network"
    layer = "background"
    led_strategy = "unified"

    def __init__(self, prometheus: PrometheusClient, mqtt_client: MQTTClient):
        self._prometheus = prometheus
        self._mqtt = mqtt_client
        self._traffic: dict[str, dict] = {}
        self._poll_task: asyncio.Task | None = None

    async def start(self):
        try:
            await self._prometheus.discover_instance_mapping()
        except Exception:
            log.exception("Instance mapping discovery failed, using fallback")
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("Network mode started (poll every %ds)", STATUS_POLL_INTERVAL)

    async def stop(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        log.info("Network mode stopped")

    async def tick(self):
        active_nodes = self._mqtt.get_active_nodes()
        if not active_nodes:
            return None

        result = {}
        for node in active_nodes:
            led_count = self._mqtt.get_node_led_count(node)
            if led_count == 0:
                continue
            traffic = self._traffic.get(node, {})
            rx_bps = traffic.get("rx_bps", 0)
            tx_bps = traffic.get("tx_bps", 0)
            rx_bright = _bps_to_brightness(rx_bps)
            tx_bright = _bps_to_brightness(tx_bps)
            leds = []
            if led_count >= 2:
                leds.append({"index": 0, "r": 0, "g": 0, "b": rx_bright})
                leds.append({"index": 1, "r": 0, "g": tx_bright, "b": 0})
            else:
                leds.append({"index": 0, "r": 0, "g": tx_bright, "b": rx_bright})
            result[node] = {"leds": leds, "effect": "solid", "params": {}}

        return result if result else None

    async def _poll_loop(self):
        while True:
            try:
                traffic = await self._prometheus.query_network_traffic()
                if traffic:
                    self._traffic = traffic
                    log.debug("Network poll: %s", {n: f"rx={d.get('rx_bps',0):.0f} tx={d.get('tx_bps',0):.0f}" for n, d in traffic.items()})
                await asyncio.sleep(STATUS_POLL_INTERVAL)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Network poll failed, keeping last known traffic")
                await asyncio.sleep(STATUS_POLL_INTERVAL)
