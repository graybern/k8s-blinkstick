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
| Python | 3.12.13 on Alpine 3.22 | Pinned exactly in Dockerfile — `python:3.12.13-alpine3.22` |
| BlinkStick | 1.2.0 from git commit `8140b9fa` | PyPI v1.2.0 has a Python 3.12 `collections.Callable` bug. Master has the fix (PR #84). Pinned to commit hash for reproducibility. |
| pyusb | 1.3.1 | USB backend for Linux |
| paho-mqtt | 2.1.0 | MQTT client (v2 API — `CallbackAPIVersion.VERSION2`) |
| libusb | OS package (`apk add libusb-dev`) | **Must be `libusb-dev`, not `libusb`** — pyusb needs the unversioned `libusb-1.0.so` symlink that only `-dev` provides |

**Additional fix 1 — collections.Callable**: After installing blinkstick, patch any remaining `collections.Callable` → `collections.abc.Callable` references:
```bash
find /path/to/site-packages -name 'blinkstick.py' -exec sed -i 's/collections\.Callable/collections.abc.Callable/g' {} +
```

**Additional fix 2 — Alpine/musl find_library**: `ctypes.util.find_library()` does not work on Alpine (musl libc lacks ldconfig/gcc lookup). pyusb cannot discover libusb even when installed. The agent patches `find_library` at startup (before any blinkstick/usb import) to fall back to a direct `/usr/lib/lib{name}.so` path check. This patch MUST run before any `import usb` or `from blinkstick import blinkstick`.

The `get_color()` method still breaks on Python 3.12+ even with master. Our code avoids calling it — we only use `set_color()`, `turn_off()`, `find_first()`, `find_all()`, `get_serial()`.

## Architecture

### Communication: MQTT

Topics:
```
blinkstick/cmd/all              # Controller → Agents: broadcast (same command to all)
blinkstick/cmd/{node-name}      # Controller → Agent: per-node command
blinkstick/cmd/cluster          # Controller → Agents: per-node colors in one message
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

### LED Addressing

Addressing is two-dimensional: the **topic** picks the node(s), the **leds array** picks which LEDs on those nodes.

| Want | How |
|------|-----|
| All 8 LEDs same color | `cmd/all` + both indexes in leds array |
| All off | `cmd/all` + `{"action":"off"}` |
| Left side all nodes | `cmd/all` + `[{"index":0,...}]` only |
| Right side all nodes | `cmd/all` + `[{"index":1,...}]` only |
| One specific node | `cmd/{node-name}` + both indexes |
| One specific LED | `cmd/{node-name}` + `[{"index":0,...}]` |
| All 8 different colors | `cmd/cluster` with per-node leds in `nodes` dict |

Omitting an index from the leds array leaves that LED unchanged.

Cluster-wide command payload (`blinkstick/cmd/cluster`) — two accepted formats for node values:
```json
{
  "action": "set",
  "nodes": {
    "octolet-control-2": [{"index": 0, "r": 255, "g": 0, "b": 0}, {"index": 1, "r": 0, "g": 0, "b": 255}],
    "octolet-control-3": {"leds": [{"index": 0, "r": 0, "g": 255, "b": 0}, {"index": 1, "r": 255, "g": 255, "b": 0}]}
  },
  "effect": "solid",
  "params": {}
}
```
Each agent extracts its own node from `nodes`, ignores the rest. Node values can be a plain leds array or an object with a `leds` key — both work. `effect` and `params` are global. Nodes not in the dict are unaffected.

### MQTT Delivery vs Execution Timing

MQTT delivery is **not** simultaneous — the broker sends to each subscriber sequentially with a few ms of jitter. For solid colors and simple effects this is invisible. For music mode (Phase 3), agents will use wall-clock scheduling: commands arrive with `"execute_at": <unix_timestamp>`, agents buffer and execute at that wall-clock moment. All Pis run NTP (systemd-timesyncd), clocks synced within ~5-10ms.

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

- **FastAPI** + Jinja2 + vanilla JS (no npm/build step, htmx removed)
- **httpx** for Prometheus/Loki/K8s API queries (not the heavy `kubernetes` pip package)
- **In-memory state only** — defaults to status mode on restart, no PVC needed
- **ConfigMap watcher** — discovers songs and mode configs by label (30s poll)
- **Engine runs in a separate daemon thread** with its own asyncio event loop (not in starlette's lifespan — see [[starlette-lifecycle-bug]])
- **WebSocket** `/ws/live` — pushes node state, health, playback info to dashboard at up to 10/sec
- **Event log** — in-memory ring buffer (200 events) recording every MQTT publish, mode switch, playback action
- **MQTT inspector** — wildcard subscription to `blinkstick/#` for debugging, stored in ring buffer
- **Prometheus metrics** — `/metrics` endpoint with `prometheus_client` (counters, gauges, histograms for mode switches, commands, clock skew, tick duration)

### Frontend Patterns

- **DOM updates, not innerHTML**: render functions create structure once, then update `style`, `textContent`, `className` in place on each WS tick. Never replace innerHTML on a per-tick basis — it kills CSS animations, resets form inputs, and defeats transitions. Topology changes (node added/removed) trigger a full rebuild via a keyed cache check.
- **Dismissed alerts**: `_dismissedAlerts` Set tracks dismissed alert IDs. Health alerts use `data-alert-id` attributes; `showToast` alerts don't, so they coexist in `#alerts` without interference.
- **apiGet returns null on error**: all callers must null-guard. `apiGet` shows a toast automatically on failure.
- **js-yaml CDN**: loaded in `music.html` only (`cdnjs.cloudflare.com/ajax/libs/js-yaml/4.1.0/js-yaml.min.js`). `editor.js` checks `window.jsyaml` and falls back to JSON.
- **No prompt()/alert()/confirm()**: use inline forms + `showToast()` instead. Blocking dialogs freeze the WS connection.
- **LED popover for direct control**: click any LED circle → inline popover with per-LED color pickers + effect dropdown. Auto-switches to direct mode. No separate Direct Control section — the LED strip is both status display and control surface. Popover closes on Escape, click outside, or same-node click.
- **Health data persists across modes**: `ModeEngine.get_health_data()` returns live data when status mode is active, cached snapshot when it's not. Dashboard shows last-known metrics in direct/music mode instead of "--".

### Music Mode

- **Pre-loaded timetable**: Controller builds the full beat sequence per node, sends ONE MQTT message per node with `play_sequence` action. Zero MQTT during playback.
- **Wall-clock sync**: Agents execute from NTP-synced clocks. `start_at` is a future unix timestamp.
- **Clock sync check**: Before song playback, controller publishes `time_check`, waits for agent clock responses. <50ms = ok, 50-200ms = warn, >200ms = block. **Skipped for presets** (ephemeral, not precision-critical). Cached 60s for songs.
- **Beat sheet format**: YAML with palette, node_order, sections, repeats, holds. Validated via Pydantic.
- **Visual editor**: Step sequencer grid (nodes as columns, beats as rows) with palette brush, section markers, Visual ↔ Code toggle. Syncs between grid and YAML.
- **Built-in presets**: chase, alternate, rainbow, flash, police — generated programmatically, no YAML needed.
- **Song store**: ConfigMaps with label `blinkstick.octolet.int/type: song`, polled every 30s.

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
- **The cluster's LEDs are the canvas** — Every mode should look intentional regardless of node count. The current deployment has 4 nodes x 2 LEDs = 8 LEDs, but the system scales to any number.
- **Dynamic discovery** — The controller discovers nodes and LED counts from MQTT agent state, not config. Node count and LEDs-per-device are never hardcoded.
- **Public repo — no secrets** — This repo is public. Never commit credentials, API keys, tokens, internal IPs, or cluster-specific secrets. Config references (service DNS, namespaces) are fine. Secrets belong in K8s Secrets on the cluster, never in code or config files here. Review every commit before pushing.

## Cluster Context (octolet)

- **K3s cluster**: 5 RPi nodes, ArgoCD at `argocd.octolet.int`
- **Ingress**: Traefik, domain pattern `*.octolet.int`
- **Observability**: Prometheus at `prometheus-stack-kube-prom-prometheus.monitoring:9090`, Loki at `loki.monitoring:3100`
- **Twingate**: Network access control — web app auth handled at network layer
- **This repo**: `graybern/k8s-blinkstick` — application code, Dockerfiles, CI
- **K8s manifests repo**: `graybern/octolet` → `apps/hardware/blinkstick/`
- **Mosquitto broker DNS**: `mosquitto.blinkstick.svc.cluster.local:1883`

## Node Names and Physical Order

Physical left-to-right order in the rack (verified 2026-09-30):

```
Position:  1 (left)         2                3                4 (right)
Node:      octolet-control-2  octolet-control-3  octolet-worker-1   octolet-worker-2
IP:        10.11.12.102       10.11.12.103       10.11.12.104       10.11.12.105
Serial:    BS051729-3.0       (TBD)              (TBD)              (TBD)
```

`octolet-control-1` (10.11.12.101) has no BlinkStick — it's the touchscreen node.

Beat sheets and sweep animations use this physical order for `node_order`.
