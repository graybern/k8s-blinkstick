import os

MQTT_BROKER = os.environ.get("MQTT_BROKER", "mosquitto.blinkstick.svc.cluster.local")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
NODE_NAME = os.environ["NODE_NAME"]
DEVICE_RETRY_INTERVAL = int(os.environ.get("DEVICE_RETRY_INTERVAL", "30"))
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "30"))
