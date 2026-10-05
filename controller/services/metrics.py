import logging
import time

log = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False
    log.warning("prometheus_client not installed, /metrics will return 501")


if HAS_PROMETHEUS:
    MODE_SWITCHES = Counter("blinkstick_mode_switches_total", "Mode switches", ["from_mode", "to_mode"])
    MQTT_MESSAGES = Counter("blinkstick_mqtt_messages_total", "MQTT messages", ["direction", "topic_prefix"])
    COMMANDS = Counter("blinkstick_commands_total", "LED commands sent", ["action", "effect"])
    PLAYBACK_DURATION = Histogram("blinkstick_playback_duration_seconds", "Playback duration")
    CLOCK_SKEW = Gauge("blinkstick_clock_skew_ms", "Clock skew per node", ["node"])
    NODES_ONLINE = Gauge("blinkstick_nodes_online", "Number of online agents")
    NODES_PRESENT = Gauge("blinkstick_nodes_present", "Number of agents with BlinkStick")
    PROM_POLL_DURATION = Histogram("blinkstick_prometheus_poll_duration_seconds", "Prometheus poll duration")
    ENGINE_TICK_DURATION = Histogram("blinkstick_engine_tick_duration_seconds", "Engine tick duration")
    WS_CONNECTIONS = Gauge("blinkstick_websocket_connections", "Active WebSocket connections")
    SONGS_TOTAL = Gauge("blinkstick_songs_total", "Number of loaded songs")
    OVERLAY_TRIGGERS = Counter("blinkstick_overlay_triggers_total", "Overlay triggers", ["name"])


def record_mode_switch(from_mode: str, to_mode: str):
    if HAS_PROMETHEUS:
        MODE_SWITCHES.labels(from_mode=from_mode, to_mode=to_mode).inc()


def record_mqtt_message(direction: str, topic: str):
    if HAS_PROMETHEUS:
        prefix = topic.split("/")[1] if "/" in topic else topic
        MQTT_MESSAGES.labels(direction=direction, topic_prefix=prefix).inc()


def record_command(action: str, effect: str = ""):
    if HAS_PROMETHEUS:
        COMMANDS.labels(action=action, effect=effect).inc()


def record_clock_skew(node: str, skew_ms: float):
    if HAS_PROMETHEUS:
        CLOCK_SKEW.labels(node=node).set(skew_ms)


def set_nodes_online(count: int):
    if HAS_PROMETHEUS:
        NODES_ONLINE.set(count)


def set_nodes_present(count: int):
    if HAS_PROMETHEUS:
        NODES_PRESENT.set(count)


def set_ws_connections(count: int):
    if HAS_PROMETHEUS:
        WS_CONNECTIONS.set(count)


def set_songs_total(count: int):
    if HAS_PROMETHEUS:
        SONGS_TOTAL.set(count)


def observe_engine_tick(duration: float):
    if HAS_PROMETHEUS:
        ENGINE_TICK_DURATION.observe(duration)


def observe_prom_poll(duration: float):
    if HAS_PROMETHEUS:
        PROM_POLL_DURATION.observe(duration)


def record_overlay_trigger(name: str):
    if HAS_PROMETHEUS:
        OVERLAY_TRIGGERS.labels(name=name).inc()


def get_metrics_response() -> tuple[bytes, str]:
    if not HAS_PROMETHEUS:
        return b"prometheus_client not installed", "text/plain"
    return generate_latest(), CONTENT_TYPE_LATEST
