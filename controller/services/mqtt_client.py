import json
import logging
import threading
import time
from collections import deque

import paho.mqtt.client as mqtt

from controller.config import MQTT_BROKER, MQTT_PORT
from controller.services import metrics

log = logging.getLogger(__name__)


class MQTTClient:
    def __init__(self, event_log=None):
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="blinkstick-controller",
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        self._lock = threading.Lock()
        self._node_registry: dict[str, dict] = {}
        self._connected = False
        self._event_log = event_log
        self._mqtt_buffer: deque = deque(maxlen=100)

    def connect(self):
        log.info("Connecting to MQTT broker at %s:%s", MQTT_BROKER, MQTT_PORT)
        self._client.connect(MQTT_BROKER, MQTT_PORT)
        self._client.loop_start()

    def disconnect(self):
        self._client.loop_stop()
        self._client.disconnect()
        log.info("Disconnected from MQTT broker")

    def is_connected(self) -> bool:
        return self._connected

    def get_node_registry(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._node_registry)

    def get_active_nodes(self) -> list[str]:
        with self._lock:
            return [
                name for name, info in self._node_registry.items()
                if info.get("present") and info.get("online")
            ]

    def get_node_led_count(self, node: str) -> int:
        with self._lock:
            info = self._node_registry.get(node, {})
            return info.get("leds", 0)

    def is_agent_online(self, node: str) -> bool:
        with self._lock:
            info = self._node_registry.get(node, {})
            return info.get("online", False)

    def get_mqtt_messages(self, limit: int = 50) -> list[dict]:
        with self._lock:
            msgs = list(self._mqtt_buffer)
        msgs.reverse()
        return msgs[:limit]

    def _record_publish(self, topic: str, payload_str: str):
        msg = {"time": time.time(), "direction": "pub", "topic": topic, "payload": payload_str[:500]}
        with self._lock:
            self._mqtt_buffer.append(msg)
        metrics.record_mqtt_message("pub", topic)
        if self._event_log:
            action = ""
            try:
                p = json.loads(payload_str)
                action = p.get("action", p.get("mode", ""))
            except Exception:
                pass
            self._event_log.append("mqtt_pub", f"{topic} {action}".strip(), target=topic.split("/")[-1] if "/" in topic else None)

    def publish_to_node(self, node: str, payload: dict):
        topic = f"blinkstick/cmd/{node}"
        payload_str = json.dumps(payload)
        self._client.publish(topic, payload_str)
        self._record_publish(topic, payload_str)

    def publish_to_all(self, payload: dict):
        topic = "blinkstick/cmd/all"
        payload_str = json.dumps(payload)
        self._client.publish(topic, payload_str)
        self._record_publish(topic, payload_str)

    def publish_cluster(self, node_commands: dict[str, dict]):
        if not node_commands:
            return
        by_effect: dict[tuple, list[str]] = {}
        for node, cmd in node_commands.items():
            key = (cmd.get("effect", "solid"), json.dumps(cmd.get("params", {}), sort_keys=True))
            by_effect.setdefault(key, []).append(node)

        for (effect, params_json), nodes in by_effect.items():
            payload = {
                "action": "set",
                "nodes": {node: node_commands[node]["leds"] for node in nodes},
                "effect": effect,
                "params": json.loads(params_json),
            }
            topic = "blinkstick/cmd/cluster"
            payload_str = json.dumps(payload)
            self._client.publish(topic, payload_str)
            self._record_publish(topic, payload_str)

    def publish_mode(self, mode_config: dict):
        topic = "blinkstick/mode/active"
        payload_str = json.dumps(mode_config)
        self._client.publish(topic, payload_str, retain=True)
        self._record_publish(topic, payload_str)

    def publish_time_check(self):
        topic = "blinkstick/cmd/all"
        payload_str = json.dumps({"action": "time_check"})
        self._client.publish(topic, payload_str)
        self._record_publish(topic, payload_str)

    def publish_to_node_raw(self, node: str, payload: dict):
        topic = f"blinkstick/cmd/{node}"
        payload_str = json.dumps(payload)
        self._client.publish(topic, payload_str)
        self._record_publish(topic, payload_str)

    def get_clock_skew(self, node: str) -> float | None:
        with self._lock:
            info = self._node_registry.get(node, {})
            ct = info.get("clock_time")
            cr = info.get("clock_received_at")
            if ct and cr:
                return abs(ct - cr) * 1000
            return None

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        log.info("Connected to MQTT broker (rc=%s)", reason_code)
        self._connected = True
        client.subscribe("blinkstick/state/+/device")
        client.subscribe("blinkstick/state/+/online")
        client.subscribe("blinkstick/state/+/clock")
        client.subscribe("blinkstick/#")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        log.warning("Disconnected from MQTT broker (rc=%s)", reason_code)
        self._connected = False

    def _on_message(self, client, userdata, msg):
        payload_str = msg.payload.decode("utf-8", errors="replace")[:500]
        with self._lock:
            self._mqtt_buffer.append({
                "time": time.time(), "direction": "sub",
                "topic": msg.topic, "payload": payload_str,
            })
        metrics.record_mqtt_message("sub", msg.topic)

        parts = msg.topic.split("/")
        if len(parts) < 4 or parts[1] != "state":
            return
        node_name = parts[2]
        subtopic = parts[3]

        with self._lock:
            if node_name not in self._node_registry:
                self._node_registry[node_name] = {
                    "present": False, "serial": None, "leds": 0,
                    "online": False, "last_seen": None,
                }

            if subtopic == "device":
                try:
                    data = json.loads(msg.payload.decode())
                    self._node_registry[node_name].update({
                        "present": data.get("present", False),
                        "serial": data.get("serial"),
                        "leds": data.get("leds", 0),
                        "last_seen": time.time(),
                    })
                    log.info("Node %s device: present=%s leds=%s",
                             node_name, data.get("present"), data.get("leds"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    log.warning("Invalid device payload from %s", node_name)

            elif subtopic == "online":
                try:
                    value = msg.payload.decode().strip()
                    online = value == "1"
                    self._node_registry[node_name]["online"] = online
                    self._node_registry[node_name]["last_seen"] = time.time()
                    log.info("Node %s online=%s", node_name, online)
                except UnicodeDecodeError:
                    log.warning("Invalid online payload from %s", node_name)

            elif subtopic == "clock":
                try:
                    data = json.loads(msg.payload.decode())
                    self._node_registry[node_name]["clock_time"] = data.get("time", 0)
                    self._node_registry[node_name]["clock_received_at"] = time.time()
                except (json.JSONDecodeError, UnicodeDecodeError):
                    log.warning("Invalid clock payload from %s", node_name)
