import logging
import time

from controller.config import PHYSICAL_NODE_ORDER
from controller.services.mqtt_client import MQTTClient

log = logging.getLogger(__name__)

SWEEP_PERIOD = 2.0
HOT_COLOR = (255, 0, 0)
TRAIL_1 = (100, 0, 0)
TRAIL_2 = (30, 0, 0)
OFF_COLOR = (0, 0, 0)


class KnightRiderMode:
    name = "knight-rider"
    layer = "background"
    led_strategy = "unified"

    def __init__(self, mqtt_client: MQTTClient):
        self._mqtt = mqtt_client

    async def start(self):
        log.info("Knight Rider mode started")

    async def stop(self):
        log.info("Knight Rider mode stopped")

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

        cycle_len = max(1, (total - 1) * 2)
        t = time.monotonic() % SWEEP_PERIOD
        raw_pos = (t / SWEEP_PERIOD) * cycle_len
        if raw_pos >= total:
            raw_pos = cycle_len - raw_pos
        hot_idx = int(round(raw_pos)) % total

        colors = [OFF_COLOR] * total
        colors[hot_idx] = HOT_COLOR
        trail_1_idx = hot_idx - 1 if hot_idx > 0 else None
        trail_2_idx = hot_idx - 2 if hot_idx > 1 else None
        if trail_1_idx is not None and colors[trail_1_idx] == OFF_COLOR:
            colors[trail_1_idx] = TRAIL_1
        if trail_2_idx is not None and colors[trail_2_idx] == OFF_COLOR:
            colors[trail_2_idx] = TRAIL_2
        trail_1_fwd = hot_idx + 1 if hot_idx < total - 1 else None
        trail_2_fwd = hot_idx + 2 if hot_idx < total - 2 else None
        if trail_1_fwd is not None and colors[trail_1_fwd] == OFF_COLOR:
            colors[trail_1_fwd] = TRAIL_1
        if trail_2_fwd is not None and colors[trail_2_fwd] == OFF_COLOR:
            colors[trail_2_fwd] = TRAIL_2

        result = {}
        for idx, (node, led_idx) in enumerate(led_positions):
            r, g, b = colors[idx]
            if node not in result:
                result[node] = {"leds": [], "effect": "solid", "params": {}}
            result[node]["leds"].append({"index": led_idx, "r": r, "g": g, "b": b})

        return result if result else None
