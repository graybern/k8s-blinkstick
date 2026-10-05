import asyncio
import logging
import threading
import time

from controller.services.mqtt_client import MQTTClient
from controller.services.prometheus import PrometheusClient
from controller.services.song_store import SongStore
from controller.services import metrics
from controller.engine.status_mode import StatusMode
from controller.engine.music_mode import MusicMode

log = logging.getLogger(__name__)

ESCALATION_SEVERITIES = {"critical"}
MORPH_DURATION = 1000
MORPH_STEPS = 50


class DirectMode:
    name = "direct"
    layer = "foreground"
    led_strategy = "unified"

    async def start(self):
        log.info("Direct control mode started")

    async def stop(self):
        log.info("Direct control mode stopped")

    async def tick(self):
        return None


class ModeEngine:
    def __init__(self, mqtt_client: MQTTClient, prometheus: PrometheusClient, song_store: SongStore | None = None):
        self._mqtt = mqtt_client
        self._prometheus = prometheus
        self._song_store = song_store
        self._background_mode: StatusMode | None = None
        self._foreground_mode = None
        self._active_mode_name = ""
        self._last_published: dict[str, dict] = {}
        self._last_health: dict[str, dict] = {}
        self._tick_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()
        self._broadcast_fn = None
        self._main_loop = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def set_broadcast(self, broadcast_fn, main_loop):
        self._broadcast_fn = broadcast_fn
        self._main_loop = main_loop

    def request_stop(self):
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    @property
    def active_mode(self) -> str:
        return self._active_mode_name

    def get_available_modes(self) -> list[dict]:
        return [
            {"name": "status", "layer": "background", "led_strategy": "unified"},
            {"name": "direct", "layer": "foreground", "led_strategy": "unified"},
            {"name": "music", "layer": "foreground", "led_strategy": "unified"},
        ]

    def get_last_published(self) -> dict[str, dict]:
        return dict(self._last_published)

    def get_status_mode(self) -> StatusMode | None:
        return self._background_mode

    def get_health_data(self) -> dict:
        if self._background_mode:
            return self._background_mode.get_node_health()
        return self._last_health

    def get_music_mode(self) -> MusicMode | None:
        if isinstance(self._foreground_mode, MusicMode):
            return self._foreground_mode
        return None

    async def start(self, default_mode: str = "status"):
        await self.set_mode(default_mode)
        self._tick_task = asyncio.create_task(self._tick_loop())
        log.info("Mode engine started with mode=%s", default_mode)

    async def stop(self):
        if self._tick_task:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
        if self._background_mode:
            await self._background_mode.stop()
        if self._foreground_mode:
            await self._foreground_mode.stop()
        log.info("Mode engine stopped")

    async def set_mode(self, name: str):
        if name == self._active_mode_name:
            return

        if self._background_mode:
            self._last_health = dict(self._background_mode.get_node_health())
            await self._background_mode.stop()
            self._background_mode = None
        if self._foreground_mode:
            await self._foreground_mode.stop()
            self._foreground_mode = None

        if name == "status":
            self._background_mode = StatusMode(self._prometheus, self._mqtt)
            await self._background_mode.start()
        elif name == "direct":
            self._foreground_mode = DirectMode()
            await self._foreground_mode.start()
        elif name == "music":
            self._foreground_mode = MusicMode(self._mqtt, self._song_store)
            await self._foreground_mode.start()
        else:
            log.warning("Unknown mode: %s", name)
            return

        old_mode = self._active_mode_name
        self._active_mode_name = name
        self._mqtt.publish_mode({
            "mode": name,
            "led_strategy": "unified",
        })
        metrics.record_mode_switch(old_mode or "none", name)
        log.info("Mode switched to: %s", name)

    async def direct_command(self, payload: dict):
        if not self._foreground_mode:
            return
        node = payload.get("node")
        cmd = {k: v for k, v in payload.items() if k != "node"}
        if node:
            self._mqtt.publish_to_node(node, cmd)
        else:
            self._mqtt.publish_to_all(cmd)

    def run_coroutine(self, coro):
        if self._loop and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return future.result(timeout=10)
        return None

    async def _tick_loop(self):
        while not self._stop_event.is_set():
            try:
                t0 = time.monotonic()
                await self._tick()
                metrics.observe_engine_tick(time.monotonic() - t0)
                registry = self._mqtt.get_node_registry()
                metrics.set_nodes_online(sum(1 for v in registry.values() if v.get("online")))
                metrics.set_nodes_present(sum(1 for v in registry.values() if v.get("present")))
                self._do_broadcast()
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Mode engine tick failed")
                await asyncio.sleep(1.0)

    async def _tick(self):
        state = None
        if self._background_mode:
            state = await self._background_mode.tick()
        if self._foreground_mode:
            fg_state = await self._foreground_mode.tick()
            if fg_state is not None:
                state = fg_state

        if state is None:
            return

        commands_to_publish = {}
        for node, cmd in state.items():
            last = self._last_published.get(node)
            if last and self._commands_equal(last, cmd):
                continue

            if last and cmd.get("_severity") in ESCALATION_SEVERITIES:
                publish_cmd = dict(cmd)
                publish_cmd.pop("_severity", None)
                commands_to_publish[node] = publish_cmd
            elif last:
                transition_cmd = {
                    "leds": cmd["leds"],
                    "effect": "morph",
                    "params": {"duration": MORPH_DURATION, "steps": MORPH_STEPS},
                }
                commands_to_publish[node] = transition_cmd
                self._schedule_final_command(node, cmd, MORPH_DURATION / 1000)
            else:
                publish_cmd = dict(cmd)
                publish_cmd.pop("_severity", None)
                commands_to_publish[node] = publish_cmd

        if commands_to_publish:
            self._mqtt.publish_cluster(commands_to_publish)

        for node, cmd in state.items():
            clean = dict(cmd)
            clean.pop("_severity", None)
            self._last_published[node] = clean

    def _do_broadcast(self):
        if not self._broadcast_fn or not self._main_loop:
            return
        registry = self._mqtt.get_node_registry()
        health_data = self.get_health_data()
        nodes = []
        for name, info in sorted(registry.items()):
            leds = []
            published = self._last_published.get(name, {})
            for led in published.get("leds", []):
                leds.append({"index": led["index"], "r": led["r"], "g": led["g"], "b": led["b"]})
            health = None
            h = health_data.get(name)
            if h:
                if self._background_mode:
                    sev, color, effect, params = self._background_mode._compute_state(name, h)
                else:
                    sev = "healthy" if h.get("up") else "critical"
                    if h.get("cpu", 0) > 0.7 or h.get("memory", 0) > 0.7 or h.get("disk", 0) > 0.8:
                        sev = "warning"
                health = {
                    "severity": sev,
                    "cpu_usage": h.get("cpu", 0),
                    "memory_usage": h.get("memory", 0),
                    "disk_usage": h.get("disk", 0),
                }
            nodes.append({
                "name": name,
                "online": info.get("online", False),
                "present": info.get("present", False),
                "device": {
                    "present": info.get("present", False),
                    "serial": info.get("serial"),
                    "leds": info.get("leds", 0),
                    "last_seen": info.get("last_seen"),
                    "clock_skew_ms": self._mqtt.get_clock_skew(name),
                },
                "leds": leds,
                "health": health,
            })
        playback = None
        music = self.get_music_mode()
        if music:
            ps = music.get_playback_state()
            playback = {"playing": ps.playing, "song": ps.song, "beat_index": ps.beat_index, "total_beats": ps.total_beats, "elapsed": ps.elapsed}
        data = {"nodes": nodes, "active_mode": self._active_mode_name, "playback": playback}
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast_fn(data), self._main_loop)
        except Exception:
            pass

    def _commands_equal(self, a: dict, b: dict) -> bool:
        return (
            a.get("leds") == b.get("leds")
            and a.get("effect") == b.get("effect")
            and a.get("params") == b.get("params")
        )

    def _schedule_final_command(self, node: str, cmd: dict, delay: float):
        async def _send_after_delay():
            try:
                await asyncio.sleep(delay + 0.1)
                final = {
                    "action": "set",
                    "leds": cmd["leds"],
                    "effect": cmd.get("effect", "solid"),
                    "params": cmd.get("params", {}),
                }
                self._mqtt.publish_to_node(node, final)
                self._last_published[node] = {
                    "leds": final["leds"],
                    "effect": final["effect"],
                    "params": final["params"],
                }
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("Failed to send final command to %s", node)

        asyncio.create_task(_send_after_delay())
