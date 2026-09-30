# BlinkStick — Implementation Phases

## Phase 1: Foundation — Agent + MQTT + Green Pulse ✅ CURRENT

**Goal:** DaemonSet agents running on cluster, controllable via MQTT. Left-to-right green pulse across 4 nodes.

### This repo (graybern/k8s-blinkstick)
- [x] CLAUDE.md — project docs
- [x] TODO.md — this file
- [ ] `agent/main.py` — MQTT subscribe, device discovery, set_color, heartbeat, preStop handler
- [ ] `agent/driver.py` — Thread-safe BlinkStick wrapper (queue.Queue + worker thread)
- [ ] `agent/effects.py` — Cancellable solid/pulse/blink/morph/off effects
- [ ] `agent/config.py` — Env vars: MQTT_BROKER, MQTT_PORT, NODE_NAME
- [ ] `Dockerfile.agent` — Multi-stage Alpine build, pin all versions
- [ ] `.github/workflows/build.yml` — ARM64 GHCR push on merge to main

### Octolet repo (graybern/octolet → apps/hardware/blinkstick/)
- [ ] `kustomization.yaml`
- [ ] `namespace.yaml`
- [ ] `daemonset.yaml` — tolerates all NoSchedule, privileged, /dev/bus/usb, preStop hook
- [ ] `deployment-mosquitto.yaml` — eclipse-mosquitto:2
- [ ] `service-mosquitto.yaml` — ClusterIP :1883
- [ ] `configmap-agent.yaml` — MQTT broker address
- [ ] `configmap-mosquitto.yaml` — listener 1883, allow_anonymous

### Verify
```bash
# Exec into mosquitto pod and send commands:
mosquitto_pub -t blinkstick/cmd/octolet-control-2 -m \
  '{"action":"set","leds":[{"index":0,"r":0,"g":255,"b":0},{"index":1,"r":0,"g":255,"b":0}],"effect":"pulse","params":{"duration":1000}}'
```

### Pause — audit agent USB reliability, MQTT delivery, graceful degradation on control-1

---

## Phase 2: Controller + Status Mode

**Goal:** Automatic cluster health visualization via web API (no UI yet).

### This repo
- [ ] `controller/main.py` — FastAPI, lifespan, /healthz, /readyz
- [ ] `controller/engine/mode_engine.py` — Layered state machine (background + overlay + foreground)
- [ ] `controller/engine/status_mode.py` — Prometheus polling, priority rules → colors
- [ ] `controller/services/mqtt_client.py` — Publisher + state subscriber
- [ ] `controller/services/prometheus.py` — httpx → Prometheus API
- [ ] `controller/services/k8s.py` — httpx + SA token → ConfigMap watch
- [ ] `controller/api/routes.py` — Mode switch, node status, direct control
- [ ] `controller/api/models.py` — Pydantic models
- [ ] `Dockerfile.controller`

### Octolet repo
- [ ] `deployment-controller.yaml`, `service-controller.yaml`
- [ ] `ingress.yaml` — blinkstick.octolet.int
- [ ] `serviceaccount.yaml`, `clusterrole.yaml`, `clusterrolebinding.yaml`
- [ ] `role-configmap-writer.yaml`, `rolebinding-configmap-writer.yaml`
- [ ] `configmap-controller.yaml` — Prometheus URL, Loki URL, default mode
- [ ] `configs/status-default.yaml` — Default threshold→color rules

### Verify
- `curl blinkstick.octolet.int/api/v1/status` → see node health
- Green LEDs = healthy. Stress a node → color changes.
- `curl -X POST blinkstick.octolet.int/api/v1/modes/active -d '{"mode":"direct"}'` → manual control

### Pause — audit Prometheus integration, mode switching, API stability

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
