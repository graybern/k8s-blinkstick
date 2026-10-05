import colorsys
import logging
import time

from controller.config import PHYSICAL_NODE_ORDER
from controller.services.mqtt_client import MQTTClient

log = logging.getLogger(__name__)

CYCLE_PERIOD = 5.0


class RainbowMode:
    name = "rainbow-wave"
    layer = "background"
    led_strategy = "unified"

    def __init__(self, mqtt_client: MQTTClient):
        self._mqtt = mqtt_client

    async def start(self):
        log.info("Rainbow Wave mode started")

    async def stop(self):
        log.info("Rainbow Wave mode stopped")

    async def tick(self):
        node_order = PHYSICAL_NODE_ORDER or sorted(self._mqtt.get_active_nodes())
        if not node_order:
            return None

        led_positions = []
        for node in node_order:
            led_count = self._mqtt.get_node_led_count(node)
            for i in range(led_count):
                led_positions.append((node, i))

        total = len(led_positions)
        if total == 0:
            return None

        base_hue = (time.monotonic() % CYCLE_PERIOD) / CYCLE_PERIOD

        result = {}
        for idx, (node, led_idx) in enumerate(led_positions):
            hue = (base_hue + idx / total) % 1.0
            rf, gf, bf = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
            r, g, b = int(rf * 255), int(gf * 255), int(bf * 255)
            if node not in result:
                result[node] = {"leds": [], "effect": "solid", "params": {}}
            result[node]["leds"].append({"index": led_idx, "r": r, "g": g, "b": b})

        return result if result else None
