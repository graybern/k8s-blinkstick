import asyncio
import logging
import threading
import time
from collections import deque

from controller.services.mqtt_client import MQTTClient
from controller.services.prometheus import PrometheusClient
from controller.services.song_store import SongStore
from controller.services.event_log import EventLog
from controller.services import metrics
from controller.engine.status_mode import StatusMode
from controller.engine.music_mode import MusicMode
from controller.engine.knight_rider_mode import KnightRiderMode
from controller.engine.rainbow_mode import RainbowMode
from controller.engine.breathing_mode import BreathingMode
from controller.engine.temperature_mode import TemperatureMode
from controller.engine.network_mode import NetworkMode
from controller.engine.morse_mode import MorseMode
from controller.engine.countdown_mode import CountdownMode
from controller.engine.twingate_mode import TwingateOverlay
from controller.engine.deploy_mode import DeployOverlay
from controller.engine.alert_mode import AlertOverlay
from controller.engine.pod_lifecycle_mode import PodLifecycleOverlay

log = logging.getLogger(__name__)

ESCALATION_SEVERITIES = {"critical"}
MORPH_DURATION = 1000
MORPH_STEPS = 50

# --- Mode Registry ---

MODE_REGISTRY: dict[str, dict] = {}


def register_mode(name: str, factory, layer: str, led_strategy: str):
    MODE_REGISTRY[name] = {
        "factory": factory,
        "layer": layer,
        "led_strategy": led_strategy,
    }


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


register_mode("status", lambda deps: StatusMode(deps["prometheus"], deps["mqtt"]), "background", "unified")
register_mode("direct", lambda deps: DirectMode(), "foreground", "unified")
register_mode("music", lambda deps: MusicMode(deps["mqtt"], deps["song_store"]), "foreground", "unified")
register_mode("knight-rider", lambda deps: KnightRiderMode(deps["mqtt"]), "background", "unified")
register_mode("rainbow-wave", lambda deps: RainbowMode(deps["mqtt"]), "background", "unified")
register_mode("breathing", lambda deps: BreathingMode(deps["prometheus"], deps["mqtt"]), "background", "unified")
register_mode("temperature", lambda deps: TemperatureMode(deps["prometheus"], deps["mqtt"]), "background", "unified")
register_mode("network", lambda deps: NetworkMode(deps["prometheus"], deps["mqtt"]), "background", "unified")
register_mode("morse", lambda deps: MorseMode(deps["mqtt"]), "foreground", "unified")
register_mode("countdown", lambda deps: CountdownMode(deps["mqtt"]), "foreground", "unified")


class ModeEngine:
    def __init__(self, mqtt_client: MQTTClient, prometheus: PrometheusClient,
                 song_store: SongStore | None = None, event_log: EventLog | None = None,
                 loki=None, alertmanager=None, k8s=None):
        self._mqtt = mqtt_client
        self._prometheus = prometheus
        self._song_store = song_store
        self._event_log = event_log
        self._loki = loki
        self._alertmanager = alertmanager
        self._k8s = k8s
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
        # Overlay state
        self._overlay_queue: deque[dict] = deque(maxlen=20)
        self._active_overlay: dict | None = None
        self._overlay_services: list = []

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

    def _get_deps(self) -> dict:
        return {
            "mqtt": self._mqtt,
            "prometheus": self._prometheus,
            "song_store": self._song_store,
            "loki": self._loki,
            "alertmanager": self._alertmanager,
            "k8s": self._k8s,
        }

    def get_available_modes(self) -> list[dict]:
        return [
            {"name": name, "layer": info["layer"], "led_strategy": info["led_strategy"]}
            for name, info in MODE_REGISTRY.items()
        ]

    def get_last_published(self) -> dict[str, dict]:
        return dict(self._last_published)

    def get_status_mode(self) -> StatusMode | None:
        if isinstance(self._background_mode, StatusMode):
            return self._background_mode
        return None

    def get_health_data(self) -> dict:
        if self._background_mode and hasattr(self._background_mode, 'get_node_health'):
            return self._background_mode.get_node_health()
        return self._last_health

    def get_music_mode(self) -> MusicMode | None:
        if isinstance(self._foreground_mode, MusicMode):
            return self._foreground_mode
        return None

    async def start(self, default_mode: str = "status"):
        await self.set_mode(default_mode)
        await self._start_overlay_services()
        self._tick_task = asyncio.create_task(self._tick_loop())
        log.info("Mode engine started with mode=%s", default_mode)

    async def stop(self):
        if self._tick_task:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
        await self._stop_overlay_services()
        if self._background_mode:
            await self._background_mode.stop()
        if self._foreground_mode:
            await self._foreground_mode.stop()
        log.info("Mode engine stopped")

    async def set_mode(self, name: str):
        if name == self._active_mode_name:
            return

        entry = MODE_REGISTRY.get(name)
        if not entry:
            log.warning("Unknown mode: %s", name)
            return

        if self._background_mode:
            if hasattr(self._background_mode, 'get_node_health'):
                self._last_health = dict(self._background_mode.get_node_health())
            await self._background_mode.stop()
            self._background_mode = None
        if self._foreground_mode:
            await self._foreground_mode.stop()
            self._foreground_mode = None

        mode = entry["factory"](self._get_deps())

        if entry["layer"] == "background":
            self._background_mode = mode
        else:
            self._foreground_mode = mode
        await mode.start()

        old_mode = self._active_mode_name
        self._active_mode_name = name
        self._mqtt.publish_mode({
            "mode": name,
            "led_strategy": entry["led_strategy"],
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

    # --- Overlay Layer ---

    def trigger_overlay(self, name: str, reason: str, priority: int,
                        duration: float, color: tuple[int, int, int],
                        effect: str = "solid", params: dict | None = None):
        now = time.time()
        overlay = {
            "name": name,
            "reason": reason,
            "priority": priority,
            "duration": duration,
            "color": color,
            "effect": effect,
            "params": params or {},
            "started_at": now,
            "expires_at": now + duration,
        }

        if self._active_overlay:
            if priority >= self._active_overlay["priority"]:
                self._overlay_queue.appendleft(self._active_overlay)
                self._active_overlay = overlay
            else:
                self._overlay_queue.append(overlay)
        else:
            self._active_overlay = overlay

        if self._event_log:
            self._event_log.append("overlay", f"{name}: {reason}", target="all")
        metrics.record_overlay_trigger(name)
        log.info("Overlay triggered: %s (priority=%d, duration=%.1fs)", name, priority, duration)

    def get_active_overlay(self) -> dict | None:
        return self._active_overlay

    def get_overlay_services_state(self) -> list[dict]:
        from controller.config import (
            OVERLAY_TWINGATE_ENABLED, OVERLAY_DEPLOY_ENABLED,
            OVERLAY_ALERT_ENABLED, OVERLAY_WEBHOOK_ENABLED,
            OVERLAY_POD_LIFECYCLE_ENABLED,
        )
        flag_map = {
            "twingate-flash": OVERLAY_TWINGATE_ENABLED,
            "deploy-wave": OVERLAY_DEPLOY_ENABLED,
            "alert-escalation": OVERLAY_ALERT_ENABLED,
            "pod-lifecycle": OVERLAY_POD_LIFECYCLE_ENABLED,
        }
        active_name = self._active_overlay["name"] if self._active_overlay else None
        result = [
            {
                "name": svc.name,
                "enabled": flag_map.get(svc.name, False),
                "active": svc.name == active_name,
            }
            for svc in self._overlay_services
        ]
        result.append({
            "name": "webhook",
            "enabled": OVERLAY_WEBHOOK_ENABLED,
            "active": active_name == "webhook",
        })
        return result

    def _check_overlay_expiry(self):
        if not self._active_overlay:
            return
        now = time.time()
        if now >= self._active_overlay["expires_at"]:
            log.info("Overlay expired: %s", self._active_overlay["name"])
            self._active_overlay = None
            while self._overlay_queue:
                candidate = self._overlay_queue.popleft()
                if candidate["expires_at"] > now:
                    self._active_overlay = candidate
                    break

    def _apply_overlay(self, state: dict[str, dict]) -> dict[str, dict]:
        if not self._active_overlay:
            return state
        overlay = self._active_overlay
        r, g, b = overlay["color"]
        merged = {}
        for node, cmd in state.items():
            leds = cmd.get("leds", [])
            led0 = next((l for l in leds if l["index"] == 0), None)
            new_leds = []
            if led0:
                new_leds.append(dict(led0))
            new_leds.append({"index": 1, "r": r, "g": g, "b": b})
            merged[node] = {
                "leds": new_leds,
                "effect": overlay["effect"],
                "params": overlay["params"],
            }
            if "_severity" in cmd:
                merged[node]["_severity"] = cmd["_severity"]
        return merged

    async def _start_overlay_services(self):
        overlays = [
            TwingateOverlay(self, self._loki),
            DeployOverlay(self, self._k8s),
            AlertOverlay(self, self._alertmanager),
            PodLifecycleOverlay(self, self._k8s),
        ]
        for overlay in overlays:
            await overlay.start()
            self._overlay_services.append(overlay)
        log.info("Overlay services started (%d active)", len(self._overlay_services))

    async def _stop_overlay_services(self):
        for overlay in self._overlay_services:
            await overlay.stop()
        self._overlay_services.clear()
        log.info("Overlay services stopped")

    # --- Tick Loop ---

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
                music = self.get_music_mode()
                await asyncio.sleep(0.2 if (music and music._playing) else 1.0)
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

        # Overlay merge — only when no foreground mode is active
        self._check_overlay_expiry()
        if self._active_overlay and not self._foreground_mode:
            state = self._apply_overlay(state)

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
        music = self.get_music_mode()
        music_state = music.get_current_led_state() if music else None
        nodes = []
        for name, info in sorted(registry.items()):
            leds = []
            published = (music_state or {}).get(name) or self._last_published.get(name, {})
            for led in published.get("leds", []):
                leds.append({"index": led["index"], "r": led["r"], "g": led["g"], "b": led["b"]})
            health = None
            h = health_data.get(name)
            if h:
                if isinstance(self._background_mode, StatusMode):
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
        overlay = self._active_overlay
        data = {
            "nodes": nodes,
            "active_mode": self._active_mode_name,
            "playback": playback,
            "active_overlay": overlay["name"] if overlay else None,
            "overlay_reason": overlay["reason"] if overlay else None,
        }
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
