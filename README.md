# BlinkStick

LED orchestration for [BlinkStick](https://www.blinkstick.com/) USB devices across a Kubernetes cluster. Deploy MQTT agents as a DaemonSet, and every node with a BlinkStick becomes a programmable RGB LED — controllable individually, by group, or cluster-wide in a single command. The controller discovers nodes and LED counts automatically; the system scales from a 2-node test bench to a full rack.

Built for Raspberry Pi K3s clusters on ARM64, but works on any K8s distribution where nodes have USB-attached BlinkStick devices.

![Architecture](docs/architecture.svg)

> *The diagram above shows the author's 5-node RPi cluster. Your deployment may have fewer or more nodes — the system adapts automatically.*

## How It Works

1. **Agent** (DaemonSet) runs on every node, discovers the local BlinkStick via USB, and subscribes to MQTT commands
2. **Mosquitto** (Deployment) brokers MQTT messages between the controller and agents
3. **Controller** (Deployment) queries Prometheus for cluster health and translates it into LED colors — or accepts direct commands via REST API

Agents publish their device state (present, serial, LED count) to MQTT. The controller discovers nodes dynamically — no hardcoded node list. Add a node with a BlinkStick, deploy the agent, and the controller picks it up on the next tick.

## Supported Devices

Any [BlinkStick](https://www.blinkstick.com/) USB device works. LED count is read from each device automatically.

| Device | LEDs | Tested |
|--------|------|--------|
| BlinkStick Nano | 2 | Yes (primary target) |
| BlinkStick Strip | 8 | Should work |
| BlinkStick Pro | Up to 64 | Should work |

## Quick Start

### Prerequisites

- A Kubernetes cluster (K3s, k3d, or any distro) with nodes that have BlinkStick USB devices
- ARM64 or AMD64 nodes (images built for ARM64; rebuild for other architectures)
- A Prometheus stack (for status mode health visualization)
- USB access: host udev rule `SUBSYSTEM=="usb", ATTR{idVendor}=="20a0", MODE="0666"` recommended

### Deploy

The system spans two repos:
- **This repo** (`k8s-blinkstick`): Application code, Dockerfiles, CI
- **Manifests repo**: K8s DaemonSet, Deployments, Mosquitto, ConfigMaps

```bash
# Push to main triggers CI → builds ARM64 images → pushes to GHCR
git push origin main

# Apply manifests (or let ArgoCD/Flux handle it)
kubectl apply -k path/to/blinkstick-manifests/
```

### Verify

```bash
# Check pods
kubectl get pods -n blinkstick

# Check agent logs
kubectl logs -n blinkstick -l app=blinkstick-agent --tail=20

# Check controller API
curl http://<controller-ingress>/api/v1/nodes    # discovered agents
curl http://<controller-ingress>/api/v1/status   # health + LED state

# Send a test command (exec into mosquitto pod)
kubectl exec -n blinkstick deploy/mosquitto -- \
  mosquitto_pub -t blinkstick/cmd/all -m \
  '{"action":"set","leds":[{"index":0,"r":0,"g":255,"b":0},{"index":1,"r":0,"g":255,"b":0}],"effect":"solid","params":{}}'
```

## Command Reference

Commands are JSON payloads published to MQTT topics.

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

Omitting an LED index from the array leaves that LED unchanged.

### Cluster-Wide

Address every node independently in a single message:

```json
{
  "action": "set",
  "nodes": {
    "node-a": [{"index": 0, "r": 255, "g": 0, "b": 0}, {"index": 1, "r": 0, "g": 0, "b": 255}],
    "node-b": [{"index": 0, "r": 0, "g": 255, "b": 0}, {"index": 1, "r": 255, "g": 255, "b": 0}]
  },
  "effect": "solid",
  "params": {}
}
```

Each agent extracts only its own node's LEDs. Nodes not in the dict are unaffected. Effect and params are global across the message.

### Common Operations

| Want | How |
|------|-----|
| All LEDs one color | `cmd/all` + both indexes |
| All LEDs off | `cmd/all` + `{"action":"off"}` |
| One side of all devices | `cmd/all` + single index in leds array |
| One specific node | `cmd/{node-name}` |
| Every node different | `cmd/cluster` with per-node leds in `nodes` dict |

## Effects

| Effect | Params | Behavior |
|--------|--------|----------|
| `solid` | — | Set color and hold |
| `pulse` | `duration` (ms, default 1000), `steps` (default 50), `repeats` (0 = infinite) | Fade up and down |
| `blink` | `delay` (ms, default 500), `repeats` (0 = infinite) | Toggle on/off |
| `morph` | `duration` (ms, default 1000), `steps` (default 50) | Transition from current color to target |
| `off` | — | Turn off |

All animations are cancellable — sending a new command immediately preempts the running effect.

## Status Mode (Controller)

When the controller runs in status mode, it polls Prometheus and maps cluster health to LED visualizations:

| State | Color | Effect | Meaning |
|-------|-------|--------|---------|
| Healthy | Green | Breathing pulse (3s) | All systems nominal |
| Warning | Amber | Solid | CPU > 70%, memory > 70%, or disk > 80% |
| Critical | Red | Fast blink (500ms) | Node down, not Ready, or resource > 90% |
| Agent offline | Dim blue | Solid | MQTT agent disconnected |

The visualization is readable from across the room — color tells you *what*, effect tells you *how urgent*.

### REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Liveness probe |
| GET | `/readyz` | Readiness probe (503 if MQTT disconnected) |
| GET | `/api/v1/status` | Node health + current LED state |
| GET | `/api/v1/modes` | Available modes |
| GET | `/api/v1/modes/active` | Current mode |
| POST | `/api/v1/modes/active` | Switch mode (`{"mode": "status"}` or `{"mode": "direct"}`) |
| POST | `/api/v1/direct` | Send LED command (direct mode only, else 409) |
| GET | `/api/v1/nodes` | Discovered node registry |

## Architecture

### Agent

- Runs as a DaemonSet on every node — discovers BlinkStick USB devices automatically
- MQTT callbacks on paho's network thread; USB commands routed through a `queue.Queue` to a dedicated worker thread
- Effects are cancellable loops using direct `set_color()` calls with a `threading.Event` between steps
- Graceful degradation: nodes without a BlinkStick publish `{"present": false}` and keep running
- On SIGTERM: sets LEDs to dim amber ("reconciling"), then exits cleanly

### Controller

- FastAPI app with asyncio tasks for the mode engine, Prometheus poller, and MQTT client
- Layered mode engine: background (status), event overlay (Phase 4), foreground (direct control)
- Dynamic node discovery from MQTT retained messages — no hardcoded node count
- LED count per device read from agent state — works with Nano (2), Strip (8), or Pro (64)
- In-memory state only — defaults to status mode on restart (fail-safe)

### MQTT Delivery

MQTT delivery is not simultaneous — the broker sends to each subscriber sequentially with a few ms of jitter. For solid colors and simple effects this is invisible. Phase 3 will add wall-clock scheduling for music synchronization, using NTP-synced clocks (~5-10ms accuracy).

## Project Structure

```
agent/
  main.py             # MQTT client, device discovery, heartbeat, shutdown
  driver.py           # Thread-safe BlinkStick wrapper (queue + worker)
  effects.py          # Cancellable solid/pulse/blink/morph/off
  config.py           # Environment variable parsing
controller/
  main.py             # FastAPI app, lifespan, healthz/readyz
  config.py           # Environment variable parsing
  api/
    routes.py         # REST endpoints
    models.py         # Pydantic models
  engine/
    mode_engine.py    # Layered state machine
    status_mode.py    # Prometheus health → LED colors
  services/
    mqtt_client.py    # MQTT publisher + state subscriber
    prometheus.py     # httpx → Prometheus API
    k8s.py            # K8s API client (SA token + httpx)
Dockerfile.agent      # Agent image (Alpine + blinkstick + pyusb)
Dockerfile.controller # Controller image (Alpine + FastAPI + httpx)
.github/workflows/
  build.yml           # ARM64 GHCR push on merge to main
docs/
  architecture.svg    # System architecture diagram
```

## Version Pinning

All dependencies are pinned to exact versions tested on cluster hardware:

**Agent**

| Dependency | Version |
|-----------|---------|
| Python | 3.12.13 on Alpine 3.22 |
| BlinkStick | git commit `8140b9fa` (master, PR #84 fix) |
| pyusb | 1.3.1 |
| paho-mqtt | 2.1.0 (v2 API) |
| libusb | `libusb-dev` (Alpine — must be `-dev` for pyusb symlink) |

**Controller**

| Dependency | Version |
|-----------|---------|
| Python | 3.12.13 on Alpine 3.22 |
| FastAPI | 0.128.8 |
| uvicorn | 0.39.0 |
| httpx | 0.28.1 |
| paho-mqtt | 2.1.0 (v2 API) |
| pydantic | 2.13.5 |
| jinja2 | 3.1.6 |

## Roadmap

See [TODO.md](TODO.md) for the full phased plan.

- **Phase 1** — Agent + MQTT + Effects (complete)
- **Phase 2** — Controller + Status Mode (complete)
- **Phase 3** — Web UI + Music Mode (beat sheets, wall-clock sync)
- **Phase 4** — Event Overlays + Creative Modes (Twingate, ArgoCD, Knight Rider)

## License

MIT
