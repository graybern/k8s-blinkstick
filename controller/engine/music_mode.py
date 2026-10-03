import asyncio
import logging
import time

from controller.api.models import BeatSheet, PlaybackState
from controller.engine.presets import generate_preset
from controller.services.mqtt_client import MQTTClient
from controller.services.song_store import SongStore

log = logging.getLogger(__name__)


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


class MusicMode:
    name = "music"
    layer = "foreground"
    led_strategy = "unified"

    def __init__(self, mqtt_client: MQTTClient, song_store: SongStore):
        self._mqtt = mqtt_client
        self._store = song_store
        self._playing = False
        self._current_song: str | None = None
        self._started_at: float | None = None
        self._beat_count = 0
        self._beat_ms = 500
        self._on_end = "status"

    async def start(self):
        log.info("Music mode started")

    async def stop(self):
        if self._playing:
            await self.stop_playback()
        log.info("Music mode stopped")

    async def tick(self):
        return None

    def get_playback_state(self) -> PlaybackState:
        beat_index = 0
        elapsed = 0.0
        if self._playing and self._started_at:
            elapsed = time.time() - self._started_at
            beat_index = max(0, min(int(elapsed / (self._beat_ms / 1000)), self._beat_count - 1))
        return PlaybackState(
            playing=self._playing,
            song=self._current_song,
            started_at=self._started_at,
            beat_index=beat_index,
            total_beats=self._beat_count,
            elapsed=elapsed,
        )

    async def play_song(self, name: str) -> dict:
        sheet = self._store.get_song(name)
        if not sheet:
            return {"error": f"Song not found: {name}"}
        return await self._play_sheet(sheet)

    async def play_preset(self, preset_name: str, bpm: int, color: str, color2: str) -> dict:
        node_order = self._mqtt.get_active_nodes()
        if not node_order:
            return {"error": "No active nodes"}
        sheet = generate_preset(preset_name, node_order, bpm, color, color2)
        return await self._play_sheet(sheet)

    async def stop_playback(self) -> dict:
        active = self._mqtt.get_active_nodes()
        for node in active:
            self._mqtt.publish_to_node_raw(node, {"action": "stop_sequence"})
        self._playing = False
        song = self._current_song
        self._current_song = None
        self._started_at = None
        log.info("Playback stopped: %s", song)
        return {"stopped": song, "on_end": self._on_end}

    async def _play_sheet(self, sheet: BeatSheet) -> dict:
        if self._playing:
            await self.stop_playback()

        sync = await self._check_clock_sync()
        max_skew = max(sync.values()) if sync else 0
        if max_skew > 200:
            return {"error": f"Clock skew too high: {max_skew:.0f}ms", "sync": sync}

        node_beats = self._build_node_beats(sheet)
        if not node_beats:
            return {"error": "No beats generated"}

        beat_ms = int(60000 / sheet.timing.bpm)
        start_at = time.time() + 3.0
        loop = sheet.timing.loop

        for node, beats in node_beats.items():
            self._mqtt.publish_to_node_raw(node, {
                "action": "play_sequence",
                "start_at": start_at,
                "beat_ms": beat_ms,
                "loop": bool(loop),
                "beats": beats,
            })

        flat_beats = next(iter(node_beats.values()), [])
        self._playing = True
        self._current_song = sheet.metadata.name
        self._started_at = start_at
        self._beat_count = len(flat_beats)
        self._beat_ms = beat_ms
        self._on_end = sheet.on_end

        log.info("Playing: %s (%d beats at %d bpm, loop=%s)",
                 sheet.metadata.name, self._beat_count, sheet.timing.bpm, loop)

        return {
            "playing": sheet.metadata.name,
            "beats": self._beat_count,
            "bpm": sheet.timing.bpm,
            "start_at": start_at,
            "clock_sync": sync,
            "warning": "Clock skew 50-200ms" if max_skew > 50 else None,
        }

    async def _check_clock_sync(self) -> dict[str, float]:
        self._mqtt.publish_time_check()
        await asyncio.sleep(2.0)
        active = self._mqtt.get_active_nodes()
        sync = {}
        for node in active:
            skew = self._mqtt.get_clock_skew(node)
            if skew is not None:
                sync[node] = skew
        return sync

    def _build_node_beats(self, sheet: BeatSheet) -> dict[str, list[dict]]:
        flat = self._expand_beats(sheet)
        if not flat:
            return {}

        active = self._mqtt.get_active_nodes()
        node_order = sheet.node_order if sheet.node_order else sorted(active)
        n = len(node_order)
        if n == 0:
            return {}

        result: dict[str, list[dict]] = {node: [] for node in node_order if node in active}

        for beat_str in flat:
            chars = list(beat_str)
            per_node = max(1, len(chars) // n) if chars else 1

            for i, node in enumerate(node_order):
                if node not in result:
                    continue
                led_count = self._mqtt.get_node_led_count(node)
                leds = []
                for led_idx in range(led_count):
                    char_idx = i * per_node + (led_idx if per_node > 1 else 0)
                    char = chars[char_idx] if char_idx < len(chars) else chars[i] if i < len(chars) else "."
                    hex_color = sheet.palette.get(char, "#000000")
                    r, g, b = hex_to_rgb(hex_color)
                    leds.append({"index": led_idx, "r": r, "g": g, "b": b})
                effect = sheet.timing.default_transition
                result[node].append({"leds": leds, "effect": effect, "params": {}})

        return result

    def _expand_beats(self, sheet: BeatSheet) -> list[str]:
        result = []
        for beat in sheet.beats:
            if isinstance(beat, str):
                result.append(beat)
            elif isinstance(beat, dict):
                if "section" in beat:
                    section = sheet.sections.get(beat["section"], [])
                    result.extend(section)
                elif "repeat" in beat:
                    count = beat.get("count", 1)
                    for _ in range(count):
                        result.append(beat["repeat"])
                elif "repeat_section" in beat:
                    section = sheet.sections.get(beat["repeat_section"], [])
                    count = beat.get("count", 1)
                    for _ in range(count):
                        result.extend(section)
                elif "colors" in beat:
                    hold = beat.get("hold", 1)
                    for _ in range(hold):
                        result.append(beat["colors"])
        return result
