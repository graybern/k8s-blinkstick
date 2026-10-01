import ctypes.util
import os

# Patch find_library for Alpine/musl — musl doesn't support the
# ldconfig/gcc lookup that glibc uses, so pyusb can't find libusb.
_orig_find = ctypes.util.find_library
def _find_library_musl(name):
    result = _orig_find(name)
    if result:
        return result
    path = f"/usr/lib/lib{name}.so"
    if os.path.exists(path):
        return path
    return None
ctypes.util.find_library = _find_library_musl

import json
import logging
import signal
import threading

import paho.mqtt.client as mqtt

from agent.config import (
    MQTT_BROKER,
    MQTT_PORT,
    NODE_NAME,
    DEVICE_RETRY_INTERVAL,
    HEARTBEAT_INTERVAL,
)
from agent.driver import BlinkStickDriver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("agent")

driver = BlinkStickDriver()
shutdown_event = threading.Event()


def on_connect(client, userdata, flags, reason_code, properties):
    log.info("Connected to MQTT broker (rc=%s)", reason_code)
    client.subscribe(f"blinkstick/cmd/{NODE_NAME}")
    client.subscribe("blinkstick/cmd/all")
    client.subscribe("blinkstick/cmd/cluster")
    client.subscribe("blinkstick/mode/active")
    client.publish(f"blinkstick/state/{NODE_NAME}/online", "1", retain=True)
    publish_device_state(client)


def on_disconnect(client, userdata, flags, reason_code, properties):
    log.warning("Disconnected from MQTT broker (rc=%s)", reason_code)


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("Invalid payload on %s", msg.topic)
        return

    if msg.topic == "blinkstick/mode/active":
        log.info("Mode update: %s", payload)
        return

    if msg.topic == "blinkstick/cmd/cluster":
        nodes = payload.get("nodes", {})
        leds = nodes.get(NODE_NAME)
        if leds is None:
            return
        payload = {
            "action": payload.get("action"),
            "leds": leds,
            "effect": payload.get("effect", "solid"),
            "params": payload.get("params", {}),
        }

    action = payload.get("action")
    if action == "set":
        driver.execute(payload)
    elif action == "off":
        driver.execute({
            "leds": payload.get("leds", [{"index": 0}, {"index": 1}]),
            "effect": "off",
            "params": {},
        })
    else:
        log.warning("Unknown action: %s", action)


def publish_device_state(client):
    info = driver.device_info()
    client.publish(
        f"blinkstick/state/{NODE_NAME}/device",
        json.dumps(info),
        retain=True,
    )


def device_retry_loop(client):
    while not shutdown_event.is_set():
        if not driver.device:
            if driver.find_device():
                publish_device_state(client)
        shutdown_event.wait(DEVICE_RETRY_INTERVAL)


def heartbeat_loop(client):
    while not shutdown_event.is_set():
        shutdown_event.wait(HEARTBEAT_INTERVAL)
        if not shutdown_event.is_set():
            publish_device_state(client)


def handle_shutdown(signum, frame):
    log.info("Shutdown signal received (%s)", signum)
    shutdown_event.set()


def main():
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    driver.find_device()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"blinkstick-agent-{NODE_NAME}",
    )
    client.will_set(f"blinkstick/state/{NODE_NAME}/online", "0", retain=True)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    log.info("Connecting to %s:%s as %s", MQTT_BROKER, MQTT_PORT, NODE_NAME)
    client.connect(MQTT_BROKER, MQTT_PORT)
    client.loop_start()

    retry_thread = threading.Thread(
        target=device_retry_loop, args=(client,), daemon=True,
    )
    retry_thread.start()

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop, args=(client,), daemon=True,
    )
    heartbeat_thread.start()

    shutdown_event.wait()

    log.info("Shutting down...")
    driver.shutdown()
    client.publish(f"blinkstick/state/{NODE_NAME}/online", "0", retain=True)
    client.disconnect()
    client.loop_stop()
    log.info("Agent stopped")


if __name__ == "__main__":
    main()
