# BlinkStick

LED orchestration for [BlinkStick](https://www.blinkstick.com/) USB devices across a Kubernetes cluster. Deploy MQTT agents as a DaemonSet, and every node with a BlinkStick becomes a programmable RGB LED — controllable individually, by group, or cluster-wide in a single command. The controller discovers nodes and LED counts automatically; the system scales from a 2-node test bench to a full rack.

Built for Raspberry Pi K3s clusters on ARM64, but works on any K8s distribution where nodes have USB-attached BlinkStick devices.

![Architecture](docs/architecture.svg)

> *The diagram above shows the author's 5-node RPi cluster. Your deployment may have fewer or more nodes — the system adapts automatically.*

## How It Works

1. **Agent** (DaemonSet) runs on every node, discovers the local BlinkStick via USB, and subscribes to MQTT commands
2. **Mosquitto** (Deployment) brokers MQTT messages between the controller and agents
3. **Controller** (Deployment) queries Prometheus for cluster health, translates it into LED colors, serves a web dashboard, and plays music patterns synchronized across nodes

Agents publish their device state (present, serial, LED count) to MQTT. The controller discovers nodes dynamically — no hardcoded node list. Add a node with a BlinkStick, deploy the agent, and the controller picks it up on the next tick.

## Web Dashboard

The controller serves a web UI at the ingress root, designed for both an 800x480 Pi touchscreen and desktop browsers.

**Three pages**: Dashboard (LED visualization + health + mode switching), Patterns (pattern library + built-in presets + beat sheet editor), Settings (MQTT inspector + event history + system info).

**LED strip as control surface**: the global LED visualization doubles as the direct control interface. Click any LED circle to open an inline popover with per-LED color pickers and effect selector — the system auto-switches to direct mode. Works on all pages.

**Five themes**: System, Light, Dark, Midnight, Terminal — persisted to localStorage, selectable from the nav bar.

**Live updates**: WebSocket at `/ws/live` pushes node state, health, and playback info. Broadcast rate increases to 5 Hz during music playback so the LED visualization animates in real time with the beat pattern. Polling fallback (3s) activates automatically when WebSocket is unavailable. Health metrics persist across mode switches.

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

# Open the dashboard
open http://<controller-ingress>/

# Check API
curl http://<controller-ingress>/api/v1/nodes     # discovered agents
curl http://<controller-ingress>/api/v1/status    # health + LED state
curl http://<controller-ingress>/api/v1/presets   # built-in patterns

# Play a preset
curl -X POST http://<controller-ingress>/api/v1/presets/chase/play
```

## Music Mode

Play synchronized LED patterns across the cluster using beat sheets — YAML files that define colors, timing, and structure.

### Beat Sheet Format

```yaml
apiVersion: blinkstick.octolet.int/v1
kind: BeatSheet
metadata:
  name: my-song
  title: "My Song"
timing:
  bpm: 120
  loop: false
on_end: status
palette:
  R: "#FF0000"
  G: "#00FF00"
  _: "#000000"
node_order:
  - node-a
  - node-b
sections:
  chorus:
    - "RG"
    - "GR"
beats:
  - "GG"
  - {section: "chorus"}
  - {repeat: "RR", count: 4}
  - {colors: "GG", hold: 2}
```

Each character in a beat string maps to a palette color. String length matches `node_order` length (one character per node). Sections, repeats, and holds expand into a flat beat list. The controller pre-loads the full timetable to each agent in a single MQTT message — zero network traffic during playback.

### NTP Clock Sync

Before playback, the controller checks clock skew across all agents:
- **< 50ms**: proceed normally
- **50-200ms**: warn in the API response
- **> 200ms**: block playback with an error

Agents execute beats from their NTP-synced wall clocks — the controller sends `start_at` as a future unix timestamp and each agent counts beats independently.

### Built-in Presets

Five patterns available instantly, no YAML required:

| Preset | Description |
|--------|-------------|
| `chase` | Light sweeps left to right |
| `alternate` | Odd and even nodes toggle |
| `rainbow` | Rotating color wheel |
| `flash` | All on, all off strobe |
| `police` | Red and blue alternating |

Play via API: `POST /api/v1/presets/{name}/play` with optional `bpm`, `color`, `color2` in the body.

### Beat Sheet Editor

The Music page includes a visual step sequencer for creating and editing beat sheets:

- **Visual mode**: nodes as columns, beats as rows. Click LED cells with a palette brush to paint colors. Add, delete, and duplicate beats. Section markers with named groups. Repeat and hold indicators.
- **Code mode**: raw YAML editor with syntax validation. Toggle between Visual ↔ Code — changes sync both ways.
- **Preview**: plays the pattern on real hardware via the API.
- **Save**: stores to Kubernetes ConfigMap via the song API.
- **Export**: downloads as a `.yaml` file.

### Song Management

Songs are stored as Kubernetes ConfigMaps with the label `blinkstick.octolet.int/type: song`. The controller polls for changes every 30 seconds and loads new songs automatically.

- **Git-synced**: add a ConfigMap to your manifests repo, ArgoCD deploys it
- **Runtime uploads**: POST YAML to `/api/v1/songs` or use the visual editor, stored as a ConfigMap labeled `source: runtime`
- **Export**: GET `/api/v1/songs/{name}/export` returns raw YAML

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

## Status Mode

When the controller runs in status mode, it polls Prometheus and maps cluster health to LED visualizations:

| State | Color | Effect | Meaning |
|-------|-------|--------|---------|
| Healthy | Green | Breathing pulse (3s) | All systems nominal |
| Warning | Amber | Solid | CPU > 70%, memory > 70%, or disk > 80% |
| Critical | Red | Fast blink (500ms) | Node down, not Ready, or resource > 90% |
| Agent offline | Dim blue | Solid | MQTT agent disconnected |

The visualization is readable from across the room — color tells you *what*, effect tells you *how urgent*.

## Observability

The controller provides enterprise-grade visibility into every layer of the system.

### Event Log

Every MQTT publish, mode switch, playback action, and alert is recorded in an in-memory ring buffer (200 events). The dashboard shows the last 10 events in an activity feed, and the Settings page has a full event history with type filters (commands, modes, alerts) and JSON export.

### MQTT Inspector

The Settings page includes a live MQTT message inspector. The controller subscribes to `blinkstick/#` (wildcard) and stores the last 100 messages with direction (→ publish, ← subscribe), topic, and payload. Messages are expandable and the inspector has a live/pause toggle.

### Prometheus Metrics

The controller exposes metrics at `/api/v1/metrics` in Prometheus exposition format:

- `blinkstick_mode_switches_total` — counter by from/to mode
- `blinkstick_mqtt_messages_total` — counter by direction and topic
- `blinkstick_commands_total` — counter by action and effect
- `blinkstick_clock_skew_ms` — gauge per node
- `blinkstick_nodes_online`, `blinkstick_nodes_present` — gauges
- `blinkstick_engine_tick_duration_seconds` — histogram
- `blinkstick_overlay_triggers_total` — counter by overlay name
- `blinkstick_websocket_connections` — gauge

### Dashboard Alerts

The dashboard renders alert banners when nodes exceed health thresholds. Warning nodes show an amber banner with the specific metric; critical nodes show a red banner. Alerts are dismissible and a notification badge appears on the Settings nav link.

## REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Liveness probe |
| GET | `/readyz` | Readiness probe (503 if MQTT disconnected) |
| GET | `/api/v1/status` | Node health + current LED state |
| GET | `/api/v1/modes` | Available modes (10 total) |
| GET | `/api/v1/modes/active` | Current mode |
| POST | `/api/v1/modes/active` | Switch mode |
| POST | `/api/v1/direct` | Send LED command (direct mode only, else 409) |
| POST | `/api/v1/off` | All LEDs off (global override, any mode) |
| GET | `/api/v1/nodes` | Discovered node registry with clock skew |
| GET | `/api/v1/songs` | List songs from ConfigMap store |
| POST | `/api/v1/songs` | Upload song (YAML or JSON body) |
| GET | `/api/v1/songs/{name}` | Song detail |
| DELETE | `/api/v1/songs/{name}` | Delete song |
| POST | `/api/v1/songs/{name}/play` | Play song |
| POST | `/api/v1/songs/stop` | Stop playback, return to on_end mode |
| GET | `/api/v1/songs/playing` | Current playback state |
| GET | `/api/v1/songs/{name}/export` | Download raw YAML |
| GET | `/api/v1/presets` | List built-in presets |
| POST | `/api/v1/presets/{name}/play` | Play preset with optional bpm/color/color2 |
| POST | `/api/v1/clock/check` | Trigger NTP clock sync check |
| GET | `/api/v1/events` | Event log (optional `?type=` filter) |
| GET | `/api/v1/mqtt/messages` | MQTT inspector (last 100 messages) |
| GET | `/api/v1/metrics` | Prometheus metrics (exposition format) |
| GET | `/api/v1/overlays` | Overlay services with enabled/active state |
| GET | `/api/v1/overlays/active` | Current active overlay or null |
| POST | `/api/v1/webhook/flash` | External LED flash trigger (optional auth) |
| POST | `/api/v1/morse/send` | Send Morse code (text + wpm) |
| POST | `/api/v1/timer/start` | Start countdown timer (duration_seconds) |
| POST | `/api/v1/timer/stop` | Stop countdown, return to status mode |
| WS | `/ws/live` | Live state updates (max 10 connections, 10/sec) |

## Architecture

### Agent

- Runs as a DaemonSet on every node — discovers BlinkStick USB devices automatically
- MQTT callbacks on paho's network thread; USB commands routed through a `queue.Queue` to a dedicated worker thread
- Effects are cancellable loops using direct `set_color()` calls with a `threading.Event` between steps
- Music playback: receives a full timetable via `play_sequence`, dedicated thread ticks through beats at wall-clock intervals
- Graceful degradation: nodes without a BlinkStick publish `{"present": false}` and keep running
- On SIGTERM: sets LEDs to dim amber ("reconciling"), then exits cleanly

### Controller

- FastAPI app with engine running in a separate daemon thread (own asyncio event loop)
- Layered mode engine: 6 background (status, knight-rider, rainbow-wave, breathing, temperature, network), event overlays (5 sources), 4 foreground (direct, music, morse, countdown)
- Mode registry pattern: `register_mode()` factory with deps dict — 10 modes self-register
- Dynamic node discovery from MQTT retained messages — no hardcoded node count
- LED count per device read from agent state — works with Nano (2), Strip (8), or Pro (64)
- Song store backed by Kubernetes ConfigMaps with 30s polling
- WebSocket broadcast from engine tick loop via cross-thread dispatch — 1 Hz normally, 5 Hz during music playback
- Health data persists across mode switches — dashboard shows last-known metrics in direct/music mode
- Live music visualization: engine computes current beat colors from the saved timetable and broadcasts them
- Event log (200-event ring buffer) and MQTT inspector (100-message buffer) for observability
- Prometheus metrics at `/api/v1/metrics` for scraping
- In-memory state only — defaults to status mode on restart (fail-safe)

### MQTT Delivery & Music Sync

MQTT delivery is not simultaneous — the broker sends to each subscriber sequentially with a few ms of jitter. For solid colors and simple effects this is invisible. For music playback, the controller pre-loads the full beat timetable to each agent in a single message with a future `start_at` timestamp. Agents execute from their NTP-synced clocks (~5-10ms accuracy), eliminating MQTT jitter entirely — zero network traffic during playback.

## Project Structure

```
agent/
  main.py             # MQTT client, device discovery, heartbeat, shutdown
  driver.py           # Thread-safe BlinkStick wrapper (queue + worker + sequence player)
  effects.py          # Cancellable solid/pulse/blink/morph/off
  config.py           # Environment variable parsing
controller/
  main.py             # FastAPI app, static files, WebSocket, engine thread
  config.py           # Environment variable parsing
  api/
    routes.py         # REST endpoints (status, modes, songs, presets, direct, events, metrics)
    web_routes.py     # HTML page routes (/, /music, /settings)
    models.py         # Pydantic models (BeatSheet, NodeHealth, PlaybackState, etc.)
    ws.py             # WebSocket connection manager (ping/pong keepalive)
  engine/
    mode_engine.py    # Layered state machine + mode registry + overlay layer
    status_mode.py    # Prometheus health → LED colors + effects
    music_mode.py     # Beat sheet player, NTP sync, preset generator
    knight_rider_mode.py  # Red scanner sweep with trailing glow
    rainbow_mode.py       # Traveling hue rotation across all LEDs
    breathing_mode.py     # CPU-proportional pulse speed + color
    temperature_mode.py   # CPU thermal heatmap (blue→green→yellow→red)
    twingate_mode.py      # Overlay: Loki connection log → cyan blink
    deploy_mode.py        # Overlay: ArgoCD sync → blue solid
    alert_mode.py         # Overlay: AlertManager critical → red blink
    pod_lifecycle_mode.py # Overlay: pod create/delete flash
    network_mode.py       # RX/TX throughput (blue/green brightness)
    morse_mode.py         # Morse code blinker
    countdown_mode.py     # Visual countdown timer
    presets.py        # Built-in pattern generators (chase, rainbow, etc.)
  services/
    mqtt_client.py    # MQTT publisher + state subscriber + clock sync + inspector
    prometheus.py     # httpx → Prometheus API (health, temperature, network)
    loki.py           # httpx → Loki API (query, query_range)
    alertmanager.py   # httpx → AlertManager API (alerts, firing critical)
    k8s.py            # K8s API client (SA token + httpx + ConfigMaps, ArgoCD, Pods)
    song_store.py     # ConfigMap-backed song cache with 30s polling
    event_log.py      # In-memory event ring buffer (200 events)
    metrics.py        # Prometheus metrics (counters, gauges, histograms)
  templates/          # Jinja2 templates (base, dashboard, patterns, settings)
web/static/
  css/style.css       # 5-theme design system (Playwright-audited)
  js/app.js           # WebSocket + polling fallback, LED rendering + popover controls, mode switching, events
  js/editor.js        # Beat sheet visual editor (step sequencer grid)
  fonts/              # Self-hosted JetBrains Mono woff2
Dockerfile.agent      # Agent image (Alpine + blinkstick + pyusb)
Dockerfile.controller # Controller image (Alpine + FastAPI + httpx + pyyaml)
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
| FastAPI | 0.115.12 |
| starlette | 0.46.2 |
| uvicorn | 0.34.2 |
| httpx | 0.28.1 |
| paho-mqtt | 2.1.0 (v2 API) |
| pydantic | 2.13.5 |
| jinja2 | 3.1.6 |
| pyyaml | 6.0.2 |
| prometheus_client | 0.21.1 |
| websockets | 14.2 |

## Roadmap

See [TODO.md](TODO.md) for the full phased plan.

- **Phase 1** — Agent + MQTT + Effects (complete)
- **Phase 2** — Controller + Status Mode (complete)
- **Phase 3** — Web UI + Music Mode (complete)
- **Phase 4a** — Observability + Beat Sheet Editor (complete)
- **UX Quality Pass** — 30-commit polish session: DOM refactor, LED popovers, live music viz, editor tools (fill row/column, drag paint, undo, move beats), Patterns rename (complete)
- **Phase 4b** — Event Overlays + Creative Modes (complete): mode registry, overlay layer, 6 background modes, 4 foreground modes, 5 overlay sources, Loki + AlertManager service clients
- **Post-Phase 4** — Network traffic mode, webhook overlay, pod lifecycle overlay, Morse code blinker, countdown timer (complete)

## Acknowledgements

This project is built on the [BlinkStick](https://www.blinkstick.com/) platform by Arvydas Juskevicius. The agent uses the [blinkstick-python](https://arvydas.github.io/blinkstick-python/) library for USB device control. See the full list of [BlinkStick API implementations](https://www.blinkstick.com/help/api-implementations) for other language bindings.

## License

MIT
