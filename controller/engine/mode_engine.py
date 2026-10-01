import asyncio
import logging

from controller.services.mqtt_client import MQTTClient
from controller.services.prometheus import PrometheusClient
from controller.engine.status_mode import StatusMode

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
    def __init__(self, mqtt_client: MQTTClient, prometheus: PrometheusClient):
        self._mqtt = mqtt_client
        self._prometheus = prometheus
        self._background_mode: StatusMode | None = None
        self._foreground_mode: DirectMode | None = None
        self._active_mode_name = ""
        self._last_published: dict[str, dict] = {}
        self._tick_task: asyncio.Task | None = None

    @property
    def active_mode(self) -> str:
        return self._active_mode_name

    def get_available_modes(self) -> list[dict]:
        return [
            {"name": "status", "layer": "background", "led_strategy": "unified"},
            {"name": "direct", "layer": "foreground", "led_strategy": "unified"},
        ]

    def get_last_published(self) -> dict[str, dict]:
        return dict(self._last_published)

    def get_status_mode(self) -> StatusMode | None:
        return self._background_mode

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
        else:
            log.warning("Unknown mode: %s", name)
            return

        self._active_mode_name = name
        self._mqtt.publish_mode({
            "mode": name,
            "led_strategy": "unified",
        })
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

    async def _tick_loop(self):
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
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

    def _commands_equal(self, a: dict, b: dict) -> bool:
        return (
            a.get("leds") == b.get("leds")
            and a.get("effect") == b.get("effect")
            and a.get("params") == b.get("params")
        )

    def _schedule_final_command(self, node: str, cmd: dict, delay: float):
        async def _send_after_delay():
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

        asyncio.create_task(_send_after_delay())
