# BlinkStick — LED Orchestration for Raspberry Pi K3s Clusters

## What This Is

A system that orchestrates BlinkStick Nano USB LEDs across a K3s cluster. Three components:
- **Agent** (DaemonSet) — runs on each node, owns the local USB device, executes LED commands via MQTT
- **Controller** (Deployment) — web app + mode engine, translates modes into per-node LED commands
- **Mosquitto** (Deployment) — MQTT broker for synchronized communication

The K8s manifests live in the **octolet** repo (`graybern/octolet` → `apps/hardware/blinkstick/`). This repo (`graybern/k8s-blinkstick`) contains the application code, Dockerfiles, and CI.

## Hardware

- **Cluster**: 5-node Raspberry Pi K3s (ARM64/aarch64), all Ubuntu 24.04
- **BlinkStick Nano** on 4 of 5 nodes: control-2, control-3, worker-1, worker-2
  - control-1 (touchscreen node, tainted `dedicated=touchscreen:NoSchedule`) has no BlinkStick
- **Each Nano has 2 independently addressable RGB LEDs** (index 0 = one side, index 1 = other side)
  - 8 LEDs total across the cluster
- **USB**: Vendor `0x20a0`, Product `0x41e5`, bcdDevice 2.02
- **Access**: via `/dev/bus/usb` using pyusb/libusb (NOT hidraw)
- **udev rule on hosts**: `SUBSYSTEM=="usb", ATTR{idVendor}=="20a0", MODE="0666"` → 0666 permissions
- **Containers need**: `privileged: true` for libusb kernel driver detach + `hostPath: /dev/bus/usb`

## Version Pinning (CRITICAL)

These exact versions are battle-tested on the cluster. Do not change without testing on real hardware.

| Dependency | Version | Why |
|-----------|---------|-----|
| Python | 3.12.x | Matches Ubuntu 24.04 system Python on the nodes |
| BlinkStick | 1.2.0 from **git master** | PyPI v1.2.0 has a Python 3.12 `collections.Callable` bug. Master has the fix (PR #84). Install with `pip install git+https://github.com/arvydas/blinkstick-python.git@master` |
| pyusb | 1.3.1 | USB backend for Linux |
| paho-mqtt | 2.1.0 | MQTT client |
| libusb | OS package (`apk add libusb`) | Required by pyusb at runtime |

**Additional fix**: After installing blinkstick from master, patch any remaining `collections.Callable` → `collections.abc.Callable` references:
```bash
find /path/to/site-packages -name 'blinkstick.py' -exec sed -i 's/collections\.Callable/collections.abc.Callable/g' {} +
```

The `get_color()` method still breaks on Python 3.12+ even with master. Our code avoids calling it — we only use `set_color()`, `turn_off()`, `find_first()`, `find_all()`, `get_serial()`.

## Architecture

### Communication: MQTT

Topics:
```
blinkstick/cmd/all              # Controller → Agents: broadcast command
blinkstick/cmd/{node-name}      # Controller → Agent: per-node command
blinkstick/state/{node}/device  # Agent → Controller: {"present":true,"serial":"BS051729-3.0","leds":2}
blinkstick/state/{node}/online  # "1" retained on connect, LWT sets "0" on disconnect
blinkstick/mode/active          # Retained: current mode configuration
```

Command payload:
```json
{
  "action": "set",
  "leds": [
    {"index": 0, "r": 0, "g": 255, "b": 0},
    {"index": 1, "r": 0, "g": 255, "b": 0}
  ],
  "effect": "solid",
  "params": {}
}
```

Effects: `solid`, `blink` (delay, repeats), `pulse` (duration, steps, repeats), `morph` (duration, steps), `off`

Music sync uses `"action": "schedule", "execute_at": <unix_timestamp>` for wall-clock aligned beats.

### Agent Design

- **Thread model**: Main thread = paho-mqtt client. USB commands go through a `queue.Queue` to a dedicated worker thread. Never touch the BlinkStick from the MQTT thread.
- **Cancellable animations**: We implement our own pulse/morph/blink loops using direct `set_color()` calls, checking a `cancel_event` between steps. Do NOT use the library's built-in `pulse()`/`morph()`/`blink()` — they're blocking with no cancellation.
- **No-device graceful degradation**: `find_first()` returns None on nodes without a BlinkStick. Agent publishes `{"present": false}`, continues running, retries every 30s.
- **Shutdown**: preStop hook sets LEDs to dim amber (= "reconciling"), then exits. Crash (SIGKILL) leaves last state.
- **Recovery**: On startup, subscribes to `blinkstick/mode/active` (retained) to learn current mode.

### Mode Layering

The controller runs a layered mode engine:

| Layer | Behavior | Examples |
|-------|----------|----------|
| **Background** | Continuous, steady-state | Status, Breathing, Temperature, Knight Rider, Rainbow |
| **Event overlay** | Brief 3-5s interrupts, fades back | Twingate Connection, Deploy Wave, Alert Escalation |
| **Foreground** | Full takeover, explicit stop | Music, Direct Control |

LED assignment strategies per mode: `unified` (both LEDs same), `split` (LED 0 = background, LED 1 = overlay), `independent` (each LED runs a different thing).

### Controller Design

- **FastAPI** + Jinja2 + htmx + vanilla JS (no npm/build step)
- **httpx** for Prometheus/Loki/K8s API queries (not the heavy `kubernetes` pip package)
- **In-memory state only** — defaults to status mode on restart, no PVC needed
- **ConfigMap watcher** — discovers songs and mode configs by label

## BlinkStick Python API (what we use)

```python
from blinkstick import blinkstick

# Discovery
stick = blinkstick.find_first()       # Returns BlinkStick or None
sticks = blinkstick.find_all()        # Returns list
stick = blinkstick.find_by_serial(s)  # Returns BlinkStick or None

# Color control — these work on Python 3.12
stick.set_color(channel=0, index=0, red=0, green=255, blue=0)
stick.set_color(channel=0, index=0, name="red")
stick.set_color(channel=0, index=0, hex="#FF0000")
stick.turn_off()

# Device info — these work
stick.get_serial()       # "BS051729-3.0"
stick.get_description()  # "BlinkStick Nano"

# DO NOT USE — broken on Python 3.12:
# stick.get_color()      # AttributeError: collections.Callable
# stick.pulse()          # Blocking, no cancellation
# stick.morph()          # Blocking, no cancellation
# stick.blink()          # Blocking, no cancellation
```

Nano addressing: `channel=0` always. `index=0` = one LED, `index=1` = other LED.

## Container Images

- `ghcr.io/graybern/k8s-blinkstick/agent` — Agent image (Alpine + Python 3.12 + blinkstick + pyusb + paho-mqtt + libusb)
- `ghcr.io/graybern/k8s-blinkstick/controller` — Controller image (Alpine + Python 3.12 + FastAPI + httpx + paho-mqtt)

Both built for `linux/arm64` only.

## Source Layout

```
CLAUDE.md               # This file
TODO.md                 # Phased implementation plan
agent/
  main.py               # MQTT client, device discovery, heartbeat, preStop
  driver.py             # Thread-safe BlinkStick wrapper (queue + worker)
  effects.py            # Cancellable animation loops
  config.py             # Env var parsing
Dockerfile.agent
controller/
  main.py               # FastAPI app, lifespan, healthz/readyz
  api/routes.py         # REST endpoints
  api/models.py         # Pydantic models
  engine/
    mode_engine.py      # Layered state machine
    status_mode.py      # Background: Prometheus health
    music_mode.py       # Foreground: beat sheet player
    breathing_mode.py   # Background: CPU-proportional pulse
    knight_rider_mode.py
    rainbow_mode.py
    deploy_mode.py      # Event overlay: ArgoCD sync
    temperature_mode.py # Background: thermal heatmap
    twingate_mode.py    # Event overlay: connection flash
    alert_mode.py       # Event overlay: AlertManager
  services/
    mqtt_client.py
    prometheus.py
    loki.py
    k8s.py
  templates/
web/static/
  css/style.css
  js/app.js
  js/htmx.min.js
Dockerfile.controller
.github/workflows/build.yml
```

## Design Philosophy

- **Declarative over imperative** — Set desired state, system converges. No fire-and-forget.
- **Agent is dumb, controller is smart** — Agent = USB driver + MQTT subscriber. All mode logic in the controller.
- **Fail safe** — No device? Noop. MQTT down? Keep last state. Controller crash? Status mode on restart.
- **GitOps configs, runtime experiments** — Permanent configs in git (octolet repo). Quick experiments via web upload. Export to promote.
- **Pin everything** — Exact versions for Python, blinkstick, pyusb, paho-mqtt. No floating deps.
- **Simple stack** — No npm, no React, no ORM, no heavy K8s client. FastAPI + htmx + httpx.
- **8 LEDs are the canvas** — 4 nodes x 2 LEDs. Every mode should look intentional at this scale.

## Cluster Context (octolet)

- **K3s cluster**: 5 RPi nodes, ArgoCD at `argocd.octolet.int`
- **Ingress**: Traefik, domain pattern `*.octolet.int`
- **Observability**: Prometheus at `prometheus-stack-kube-prom-prometheus.monitoring:9090`, Loki at `loki.monitoring:3100`
- **Twingate**: Network access control — web app auth handled at network layer
- **This repo**: `graybern/k8s-blinkstick` — application code, Dockerfiles, CI
- **K8s manifests repo**: `graybern/octolet` → `apps/hardware/blinkstick/`
- **Mosquitto broker DNS**: `mosquitto.blinkstick.svc.cluster.local:1883`

## Node Names (for MQTT topics and beat sheets)

```
octolet-control-1  # No BlinkStick (touchscreen node)
octolet-control-2  # BlinkStick Nano, serial BS051729-3.0
octolet-control-3  # BlinkStick Nano
octolet-worker-1   # BlinkStick Nano
octolet-worker-2   # BlinkStick Nano
```
