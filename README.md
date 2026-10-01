# BlinkStick

LED orchestration for [BlinkStick Nano](https://www.blinkstick.com/products/blinkstick-nano) USB devices across a Raspberry Pi K3s cluster. Eight individually controllable RGB LEDs (4 nodes x 2 LEDs each), driven via MQTT with cancellable animation effects.

![Architecture](docs/architecture.svg)

## Hardware

| Node | Role | BlinkStick | Position |
|------|------|-----------|----------|
| octolet-control-1 | Touchscreen (tainted) | None | — |
| octolet-control-2 | Control plane | Nano (BS051729-3.0) | Left |
| octolet-control-3 | Control plane | Nano | Center-left |
| octolet-worker-1 | Worker | Nano | Center-right |
| octolet-worker-2 | Worker | Nano | Right |

- **Cluster**: 5-node Raspberry Pi K3s, all ARM64/Ubuntu 24.04
- **Each Nano**: 2 independently addressable RGB LEDs (index 0, index 1)
- **USB access**: Privileged containers with `/dev/bus/usb` host mount

## Quick Start

The system has two repos:
- **This repo** (`graybern/k8s-blinkstick`): Application code, Dockerfiles, CI
- **Manifests** (`graybern/octolet` → `apps/hardware/blinkstick/`): K8s DaemonSet, Mosquitto, ConfigMaps

### Deploy

```bash
# 1. Push to main triggers CI → builds ARM64 image → pushes to GHCR
git push origin main

# 2. ArgoCD deploys from octolet repo automatically
# Or manually: kubectl apply -k apps/hardware/blinkstick/
```

### Verify

```bash
# Check all pods are running
kubectl get pods -n blinkstick

# Check agent logs
kubectl logs -n blinkstick -l app=blinkstick-agent --tail=20

# Send a test command (exec into mosquitto pod)
kubectl exec -n blinkstick deploy/mosquitto -- \
  mosquitto_pub -t blinkstick/cmd/all -m \
  '{"action":"set","leds":[{"index":0,"r":0,"g":255,"b":0},{"index":1,"r":0,"g":255,"b":0}],"effect":"solid","params":{}}'
```

## Command Reference

Commands are JSON payloads published to MQTT topics. The broker is at `mosquitto.blinkstick.svc.cluster.local:1883`.

### Topics

| Topic | Scope |
|-------|-------|
| `blinkstick/cmd/all` | Broadcast — same command to all agents |
| `blinkstick/cmd/{node-name}` | Single node |
| `blinkstick/cmd/cluster` | Cluster-wide — different colors per node in one message |

### Single Node / Broadcast

```json
{
  "action": "set",
  "leds": [
    {"index": 0, "r": 0, "g": 255, "b": 0},
    {"index": 1, "r": 0, "g": 0, "b": 255}
  ],
  "effect": "solid",
  "params": {}
}
```

### Cluster-Wide (All 8 LEDs Independently)

```json
{
  "action": "set",
  "nodes": {
    "octolet-control-2": [{"index": 0, "r": 255, "g": 0, "b": 0}, {"index": 1, "r": 0, "g": 0, "b": 255}],
    "octolet-control-3": [{"index": 0, "r": 0, "g": 255, "b": 0}, {"index": 1, "r": 255, "g": 255, "b": 0}],
    "octolet-worker-1":  [{"index": 0, "r": 255, "g": 0, "b": 255}, {"index": 1, "r": 0, "g": 255, "b": 255}],
    "octolet-worker-2":  [{"index": 0, "r": 255, "g": 128, "b": 0}, {"index": 1, "r": 128, "g": 0, "b": 255}]
  },
  "effect": "solid",
  "params": {}
}
```

Each agent extracts only its own node's LEDs. Nodes not in the dict are unaffected.

### Common Operations

| Want | Topic | Payload |
|------|-------|---------|
| All LEDs green | `cmd/all` | `{"action":"set","leds":[{"index":0,"r":0,"g":255,"b":0},{"index":1,"r":0,"g":255,"b":0}],"effect":"solid","params":{}}` |
| All LEDs off | `cmd/all` | `{"action":"off"}` |
| Left side only | `cmd/all` | `{"action":"set","leds":[{"index":0,"r":255,"g":0,"b":0}],"effect":"solid","params":{}}` |
| Right side only | `cmd/all` | `{"action":"set","leds":[{"index":1,"r":0,"g":0,"b":255}],"effect":"solid","params":{}}` |
| One node | `cmd/octolet-control-2` | Same as broadcast format |
| One LED | `cmd/octolet-control-2` | `{"action":"set","leds":[{"index":0,"r":255,"g":0,"b":0}],"effect":"solid","params":{}}` |

## Effects

| Effect | Params | Behavior |
|--------|--------|----------|
| `solid` | — | Set color and hold |
| `pulse` | `duration` (ms, default 1000), `steps` (default 50), `repeats` (0 = infinite) | Fade up and down |
| `blink` | `delay` (ms, default 500), `repeats` (0 = infinite) | Toggle on/off |
| `morph` | `duration` (ms, default 1000), `steps` (default 50) | Transition from current color to target |
| `off` | — | Turn off |

All animations are cancellable — sending a new command immediately preempts the running effect.

## Architecture

### Components

- **Agent** (DaemonSet) — Runs on every node. Subscribes to MQTT, drives the local USB BlinkStick via a thread-safe worker queue. No mode logic — just a USB driver.
- **Mosquitto** (Deployment) — MQTT broker. Routes commands from publishers to agent subscribers.
- **Controller** (Phase 2) — Will add a REST API, mode engine, and Prometheus-driven health visualization.

### Agent Design

- MQTT callbacks run on paho's network thread; USB commands go through a `queue.Queue` to a dedicated worker thread (never touch USB from the MQTT thread)
- Effects are cancellable loops using direct `set_color()` calls with a `threading.Event` checked between steps
- Graceful degradation: nodes without a BlinkStick publish `{"present": false}` and retry every 30s
- On SIGTERM: sets LEDs to dim amber ("reconciling"), then exits cleanly
- On startup: subscribes to `blinkstick/mode/active` (retained) for state recovery

### MQTT Delivery

MQTT delivery is not simultaneous — the broker sends to each subscriber sequentially with a few ms of jitter. For solid colors and simple effects this is invisible. Phase 3 will add wall-clock scheduling (`"execute_at": <unix_timestamp>`) for music synchronization, using NTP-synced clocks (~5-10ms accuracy).

## Project Structure

```
agent/
  main.py           # MQTT client, device discovery, heartbeat, shutdown
  driver.py         # Thread-safe BlinkStick wrapper (queue + worker)
  effects.py        # Cancellable solid/pulse/blink/morph/off
  config.py         # Environment variable parsing
Dockerfile.agent    # Multi-stage Alpine build, all versions pinned
.github/workflows/
  build.yml         # ARM64 GHCR push on merge to main
docs/
  architecture.svg  # System architecture diagram
```

## Version Pinning

All dependencies are pinned to exact versions tested on the cluster hardware:

| Dependency | Version |
|-----------|---------|
| Python | 3.12.13 on Alpine 3.22 |
| BlinkStick | git commit `8140b9fa` (master, PR #84 fix) |
| pyusb | 1.3.1 |
| paho-mqtt | 2.1.0 (v2 API) |
| libusb | `libusb-dev` (Alpine — must be `-dev` for pyusb symlink) |

## Roadmap

See [TODO.md](TODO.md) for the full phased plan.

- **Phase 1** — Agent + MQTT + Effects (complete)
- **Phase 2** — Controller + Status Mode (Prometheus health visualization)
- **Phase 3** — Web UI + Music Mode (beat sheets, wall-clock sync)
- **Phase 4** — Event Overlays + Creative Modes (Twingate, ArgoCD, Knight Rider)

## License

MIT
