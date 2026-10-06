import logging
import time

from controller.config import PHYSICAL_NODE_ORDER
from controller.services.mqtt_client import MQTTClient

log = logging.getLogger(__name__)

GREEN = (0, 255, 0)
RED = (255, 0, 0)
WHITE = (255, 255, 255)
OFF = (0, 0, 0)
FINAL_WARN_SECS = 10
FLASH_COUNT = 3
FLASH_DURATION = 0.8


class CountdownMode:
    name = "countdown"
    layer = "foreground"
    led_strategy = "unified"

    def __init__(self, mqtt_client: MQTTClient):
        self._mqtt = mqtt_client
        self._duration: float = 0
        self._started_at: float | None = None
        self._finished = False

    async def start(self):
        log.info("Countdown mode started")

    async def stop(self):
        self._started_at = None
        self._duration = 0
        self._finished = False
        log.info("Countdown mode stopped")

    def configure(self, duration_seconds: float):
        self._duration = duration_seconds
        self._started_at = time.monotonic()
        self._finished = False
        log.info("Countdown: %ds timer started", int(duration_seconds))

    async def tick(self):
        if self._started_at is None or self._duration <= 0:
            return None

        node_order = PHYSICAL_NODE_ORDER or sorted(self._mqtt.get_active_nodes())
        if not node_order:
            return None

        led_positions = []
        for node in node_order:
            led_count = self._mqtt.get_node_led_count(node)
            for i in range(led_count):
                led_positions.append((node, i))
        total_leds = len(led_positions)
        if total_leds == 0:
            return None

        elapsed = time.monotonic() - self._started_at
        remaining = self._duration - elapsed

        if remaining <= 0:
            flash_elapsed = elapsed - self._duration
            if flash_elapsed > FLASH_COUNT * FLASH_DURATION * 2:
                self._finished = True
                return self._build_state(led_positions, [OFF] * total_leds)
            flash_on = int(flash_elapsed / FLASH_DURATION) % 2 == 0
            color = WHITE if flash_on else OFF
            return self._build_state(led_positions, [color] * total_leds)

        leds_on = max(0, int((remaining / self._duration) * total_leds + 0.5))
        in_warning = remaining <= FINAL_WARN_SECS
        blink_on = int(time.monotonic() * 4) % 2 == 0

        colors = []
        for i in range(total_leds):
            if i < leds_on:
                if in_warning:
                    colors.append(RED if blink_on else OFF)
                else:
                    colors.append(GREEN)
            else:
                colors.append(OFF)

        return self._build_state(led_positions, colors)

    def _build_state(self, led_positions, colors):
        result = {}
        for idx, (node, led_idx) in enumerate(led_positions):
            r, g, b = colors[idx]
            if node not in result:
                result[node] = {"leds": [], "effect": "solid", "params": {}}
            result[node]["leds"].append({"index": led_idx, "r": r, "g": g, "b": b})
        return result if result else None
