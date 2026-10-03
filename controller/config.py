import os

MQTT_BROKER = os.environ.get("MQTT_BROKER", "mosquitto.blinkstick.svc.cluster.local")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus-stack-kube-prom-prometheus.monitoring:9090")
STATUS_POLL_INTERVAL = int(os.environ.get("STATUS_POLL_INTERVAL", "15"))
DEFAULT_MODE = os.environ.get("DEFAULT_MODE", "status")
PROMETHEUS_TIMEOUT = int(os.environ.get("PROMETHEUS_TIMEOUT", "10"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
K8S_NAMESPACE = os.environ.get("K8S_NAMESPACE", "blinkstick")
SONG_POLL_INTERVAL = int(os.environ.get("SONG_POLL_INTERVAL", "30"))

_node_order = os.environ.get("PHYSICAL_NODE_ORDER", "")
PHYSICAL_NODE_ORDER = [n.strip() for n in _node_order.split(",") if n.strip()] or None

_fallback_map = os.environ.get("FALLBACK_INSTANCE_MAP", "")
FALLBACK_INSTANCE_MAP: dict[str, str] = {}
if _fallback_map:
    for pair in _fallback_map.split(","):
        if "=" in pair:
            instance, node = pair.strip().split("=", 1)
            FALLBACK_INSTANCE_MAP[instance.strip()] = node.strip()
else:
    FALLBACK_INSTANCE_MAP = {
        "10.11.12.101:9100": "octolet-control-1",
        "10.11.12.102:9100": "octolet-control-2",
        "10.11.12.103:9100": "octolet-control-3",
        "10.11.12.104:9100": "octolet-worker-1",
        "10.11.12.105:9100": "octolet-worker-2",
    }
