import logging
import time

from controller.services.mqtt_client import MQTTClient

log = logging.getLogger(__name__)

MORSE_TABLE = {
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.', 'F': '..-.',
    'G': '--.', 'H': '....', 'I': '..', 'J': '.---', 'K': '-.-', 'L': '.-..',
    'M': '--', 'N': '-.', 'O': '---', 'P': '.--.', 'Q': '--.-', 'R': '.-.',
    'S': '...', 'T': '-', 'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-',
    'Y': '-.--', 'Z': '--..', '0': '-----', '1': '.----', '2': '..---',
    '3': '...--', '4': '....-', '5': '.....', '6': '-....', '7': '--...',
    '8': '---..', '9': '----.', '.': '.-.-.-', ',': '--..--', '?': '..--..',
    '!': '-.-.--', '/': '-..-.', '(': '-.--.', ')': '-.--.-', '&': '.-...',
    ':': '---...', ';': '-.-.-.', '=': '-...-', '+': '.-.-.', '-': '-....-',
    '_': '..--.-', '"': '.-..-.', "'": '.----.', '@': '.--.-.',
}

ON_COLOR = (255, 255, 255)
OFF_COLOR = (0, 0, 0)


def _build_timetable(text: str, unit_ms: float) -> list[tuple[float, bool]]:
    events = []
    t = 0.0
    for i, char in enumerate(text.upper()):
        if char == ' ':
            t += unit_ms * 7 / 1000
            continue
        code = MORSE_TABLE.get(char)
        if not code:
            continue
        if i > 0 and text[i - 1] != ' ' and events:
            t += unit_ms * 3 / 1000
        for j, symbol in enumerate(code):
            if j > 0:
                t += unit_ms / 1000
            duration = (unit_ms * 3 if symbol == '-' else unit_ms) / 1000
            events.append((t, True))
            t += duration
            events.append((t, False))
    return events


class MorseMode:
    name = "morse"
    layer = "foreground"
    led_strategy = "unified"

    def __init__(self, mqtt_client: MQTTClient):
        self._mqtt = mqtt_client
        self._timetable: list[tuple[float, bool]] = []
        self._started_at: float | None = None
        self._last_state: bool | None = None

    async def start(self):
        log.info("Morse code mode started")

    async def stop(self):
        self._timetable = []
        self._started_at = None
        self._last_state = None
        log.info("Morse code mode stopped")

    def send(self, text: str, wpm: int = 15):
        unit_ms = 1200 / max(1, wpm)
        self._timetable = _build_timetable(text, unit_ms)
        self._started_at = time.monotonic()
        self._last_state = None
        log.info("Morse: '%s' at %d WPM (%d events)", text, wpm, len(self._timetable))

    async def tick(self):
        if not self._timetable or self._started_at is None:
            return None

        elapsed = time.monotonic() - self._started_at
        on = False
        for t, state in reversed(self._timetable):
            if elapsed >= t:
                on = state
                break

        if elapsed > self._timetable[-1][0] + 0.5:
            self._timetable = []
            self._started_at = None
            on = False

        if on == self._last_state:
            return None
        self._last_state = on

        active_nodes = self._mqtt.get_active_nodes()
        if not active_nodes:
            return None

        r, g, b = ON_COLOR if on else OFF_COLOR
        result = {}
        for node in active_nodes:
            led_count = self._mqtt.get_node_led_count(node)
            if led_count == 0:
                continue
            leds = [{"index": i, "r": r, "g": g, "b": b} for i in range(led_count)]
            result[node] = {"leds": leds, "effect": "solid", "params": {}}

        return result if result else None
