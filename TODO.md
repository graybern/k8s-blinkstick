# BlinkStick — Implementation Phases

## Phase 1: Foundation — Agent + MQTT + Green Pulse ✅ COMPLETE (2026-09-30)

**Goal:** DaemonSet agents running on cluster, controllable via MQTT. Left-to-right green pulse across 4 nodes.

### This repo (graybern/k8s-blinkstick)
- [x] CLAUDE.md — project docs
- [x] TODO.md — this file
- [x] `agent/main.py` — MQTT subscribe, device discovery, set_color, heartbeat, preStop handler
- [x] `agent/driver.py` — Thread-safe BlinkStick wrapper (queue.Queue + worker thread)
- [x] `agent/effects.py` — Cancellable solid/pulse/blink/morph/off effects
- [x] `agent/config.py` — Env vars: MQTT_BROKER, MQTT_PORT, NODE_NAME
- [x] `Dockerfile.agent` — Multi-stage Alpine build, pin all versions
- [x] `.github/workflows/build.yml` — ARM64 GHCR push on merge to main

### Octolet repo (graybern/octolet → apps/hardware/blinkstick/)
- [x] `kustomization.yaml`
- [x] `namespace.yaml`
- [x] `daemonset.yaml` — tolerates all taints, privileged, /dev/bus/usb
- [x] `deployment-mosquitto.yaml` — eclipse-mosquitto:2
- [x] `service-mosquitto.yaml` — ClusterIP :1883
- [x] `configmap-agent.yaml` — MQTT broker address
- [x] `configmap-mosquitto.yaml` — listener 1883, allow_anonymous

### Lessons learned
- Alpine/musl: `ctypes.util.find_library` doesn't work — must monkey-patch before importing pyusb
- `libusb-dev` needed in runtime stage (not just `libusb`) for the unversioned `.so` symlink
- Physical node order (L→R): control-2, control-3, worker-1, worker-2
- Python stdout buffering makes logs appear empty — not a crash indicator, check restart count instead

### Verified on hardware (2026-09-30)
- All 5 agent pods running (4 with BlinkStick, control-1 gracefully degraded with `{"present": false}`)
- Mosquitto broker reachable at `mosquitto.blinkstick.svc.cluster.local:1883`
- Per-node commands (`blinkstick/cmd/{node}`) — sets colors on both LEDs independently
- Broadcast commands (`blinkstick/cmd/all`) — all 8 LEDs at once
- Cluster-wide commands (`blinkstick/cmd/cluster`) — all 8 LEDs to different colors in one message
- Effects: solid, off verified via MQTT (pulse/blink/morph available, untested via MQTT)
- USB disconnect recovery via USBError catch + device retry loop
- No preStop hook needed — agent handles SIGTERM directly (amber + clean exit)
- `terminationGracePeriodSeconds: 10` gives headroom for worker join (5s timeout)
- `PYTHONUNBUFFERED=1` ensures immediate log visibility

```bash
# Verified command format:
mosquitto_pub -t blinkstick/cmd/octolet-control-2 -m \
  '{"action":"set","leds":[{"index":0,"r":0,"g":255,"b":0},{"index":1,"r":0,"g":255,"b":0}],"effect":"pulse","params":{"duration":1000}}'
```

---

## Phase 2: Controller + Status Mode ✅ COMPLETE (2026-10-02)

**Goal:** Automatic cluster health visualization via web API (no UI yet).

### This repo
- [x] `controller/main.py` — FastAPI, lifespan, /healthz, /readyz
- [x] `controller/engine/mode_engine.py` — Layered state machine (background + overlay + foreground)
- [x] `controller/engine/status_mode.py` — Prometheus polling, priority rules → colors + effects
- [x] `controller/services/mqtt_client.py` — Publisher + state subscriber + dynamic node discovery
- [x] `controller/services/prometheus.py` — httpx → Prometheus API + dynamic instance mapping
- [x] `controller/services/k8s.py` — httpx + SA token → ConfigMap listing (Phase 3 scaffold)
- [x] `controller/api/routes.py` — Mode switch, node status, direct control
- [x] `controller/api/models.py` — Pydantic models
- [x] `Dockerfile.controller`

### Octolet repo
- [x] `deployment-controller.yaml`, `service-controller.yaml`
- [x] `ingress.yaml` — blinkstick.octolet.int
- [x] `serviceaccount.yaml`, `clusterrole.yaml`, `clusterrolebinding.yaml`
- [x] `role.yaml`, `rolebinding.yaml` — namespace-scoped ConfigMap CRUD
- [x] `configmap-controller.yaml` — Prometheus URL, default mode

### Verified
- `/api/v1/nodes` — all 5 nodes discovered, 4 with BlinkStick serials, all online
- `/api/v1/status` — green pulse = healthy on all nodes
- `/api/v1/modes` — status (active) + direct available
- Controller stable: 0 restarts, 20+ minutes uptime

### Lessons learned
- Starlette lifespan (both `@asynccontextmanager` and `@app.on_event`) has a CancelledError bug — bypass entirely by owning lifecycle in `main()`
- uvloop conflicts with `asyncio.new_event_loop()` in threads — use plain `uvicorn` not `uvicorn[standard]`
- Engine runs in its own daemon thread with its own event loop, uvicorn runs in main thread
- Liveness probe removed — readiness probe only for now (liveness was killing the pod during startup)
- GHCR packages must be set to public manually (per-package, not per-repo)
- containerd aggressively caches `:latest` tags — use SHA tags or scale-to-zero to force fresh pulls
- K3s client certs expire after 1 year — regenerate from `/etc/rancher/k3s/k3s.yaml` on a control plane node

---

## Phase 3: Web UI + Music Mode

**Goal:** Full web interface, beat sheet library, GitOps config pipeline.

### This repo
- [ ] `controller/engine/music_mode.py` — Beat sheet player, pre-buffered wall-clock scheduling
- [ ] `controller/templates/` — Dashboard, music library, mode config, direct control, settings
- [ ] `web/static/css/style.css` — Use /frontend-design skill for opinionated approach
- [ ] `web/static/js/app.js` — WebSocket client, UI logic
- [ ] `web/static/js/htmx.min.js`
- [ ] Agent: add `schedule` action for wall-clock beat sync

### Octolet repo
- [ ] `songs/jingle-bells.yaml` — First beat sheet
- [ ] Update kustomization.yaml with configMapGenerator entries

### Verify
- Play beat sheet → synchronized LEDs across 4 nodes
- Upload runtime config via web → appears in library
- Export runtime config → downloadable YAML

### Pause — audit web UX, music sync accuracy, config pipeline

---

## Phase 4: Event Overlays + Creative Modes

**Goal:** Layered overlays, screensaver modes, Twingate integration.

### Background modes
- [ ] Knight Rider (scanner sweep)
- [ ] Rainbow Wave (traveling hue rotation)
- [ ] Breathing (CPU-proportional pulse)
- [ ] Temperature Heatmap (CPU temp → color spectrum)

### Event overlays
- [ ] Twingate Connection (Loki log query → cyan flash on connect)
- [ ] Deploy Wave (ArgoCD Application watch → sweep on sync)
- [ ] Alert Escalation (AlertManager API → red strobe on critical)

### Polish
- [ ] Split LED assignment (background on LED 0, overlay on LED 1)
- [ ] Homepage integration (Ingress annotations in octolet)
- [ ] Optional: Grafana dashboard for agent health

---

## Future (post-Phase 4)
- [ ] Pod Lifecycle overlay
- [ ] Countdown Timer foreground mode
- [ ] Simon Says game
- [ ] Morse Code message blinker
- [ ] Network Traffic background mode
- [ ] Notification Flash webhook overlay
