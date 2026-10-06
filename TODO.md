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

## Phase 3: Web UI + Music Mode ✅ COMPLETE (2026-10-03)

**Goal:** Full web interface, beat sheet library, GitOps config pipeline.

### This repo
- [x] Beat sheet format with sections/repeats (BeatSheet Pydantic model with validation)
- [x] `controller/engine/music_mode.py` — Pre-load full timetable to agents, NTP clock sync check
- [x] Built-in preset patterns (chase, alternate, rainbow, flash, police) — zero-YAML quick start
- [x] `controller/api/routes.py` — Song CRUD, presets, template routes, WebSocket /ws/live
- [x] `controller/api/models.py` — BeatSheet Pydantic validation model
- [x] `controller/services/k8s.py` — ConfigMap CRUD + polling watcher + immediate startup load
- [x] `controller/engine/mode_engine.py` — Register music mode + WebSocket broadcast
- [x] `controller/main.py` — Mount static files, Jinja2 templates, WebSocket endpoint
- [x] `controller/templates/` — base, dashboard, music, modes, direct, settings (5 themes, 800x480+desktop)
- [x] `web/static/` — CSS (5 themes, Playwright-audited), JS (WebSocket + LED rendering), htmx 2.0.4, self-hosted JetBrains Mono
- [x] Agent: `play_sequence` action (full timetable, NTP tick), `stop_sequence`, `time_check`
- [x] `Dockerfile.controller` — Add pyyaml, COPY web/

### Octolet repo
- [x] `songs/jingle-bells.yaml` — ConfigMap with labels, sections/repeats format
- [x] Update kustomization.yaml — added as plain resource (not configMapGenerator)

### Verify
- Play beat sheet via web UI → synchronized LEDs across 4 nodes (NTP-synced)
- Quick pattern (preset) via web UI → instant LED effect, no YAML
- Upload runtime beat sheet → appears in library → play → export YAML
- WebSocket live updates on dashboard
- Clock sync check passes before playback

### Pause — audit web UX, music sync accuracy, config pipeline

---

## Phase 4a: UI Fixes + Observability + Beat Sheet Editor ✅ COMPLETE (2026-10-03)

**Goal:** Fix live site gaps vs mockup, add enterprise-grade observability, add visual beat sheet editor.

### Commit 1 — Backend services + controller fixes
- [x] `controller/services/event_log.py` — Ring buffer event log (200 events, typed, timestamped)
- [x] `controller/services/metrics.py` — Prometheus metrics at /metrics (prometheus_client)
- [x] `controller/services/mqtt_client.py` — Wildcard subscription for MQTT inspector, instrument publishes
- [x] `controller/engine/music_mode.py` — Skip clock sync for presets (2.1s → ~500ms), cache sync results 60s
- [x] `controller/engine/mode_engine.py` — Instrument tick with metrics, log events
- [x] `controller/api/routes.py` — Add /events, /mqtt/messages, /metrics endpoints
- [x] `controller/main.py` — Wire event_log, metrics
- [x] `controller/config.py` — LOG_FORMAT env var
- [x] `Dockerfile.controller` — Add prometheus_client

### Commit 2 — Dashboard + Settings UI fixes
- [x] `web/static/js/app.js` — Fix renderPanels (clock sync), renderNodes (clock col, roles, warn-stripe, expandable detail), add renderAlerts, renderEvents, WS countdown timer
- [x] `web/static/css/style.css` — Event log styles, MQTT inspector styles, info grid styles, filter chips
- [x] `controller/templates/dashboard.html` — Add #alerts, #events sections
- [x] `controller/templates/settings.html` — MQTT inspector, event history, controller info grid
- [x] `controller/templates/direct.html` — Add morph effect, all-nodes broadcast

### Commit 3 — Beat sheet visual editor
- [x] `web/static/js/editor.js` — Step sequencer grid, palette brush, cell click, YAML ↔ visual sync, beat CRUD, section markers, repeat/hold
- [x] `controller/templates/music.html` — Replace textarea with visual editor + code tab + metadata/timing fields
- [x] `web/static/css/style.css` — Editor grid styles, palette bar, section markers

### Octolet repo
- [x] Add Prometheus ServiceMonitor for /metrics scraping (octolet commit 824e718)
- [x] Update controller RBAC if needed for ConfigMap CRUD (octolet commit 824e718)

### Verify
- Dashboard: alert banners, clock sync panel, full node table with roles + expandable detail
- Music: preset play instant (<100ms), BPM/color inputs work, visual editor creates/edits/exports beat sheets
- Settings: MQTT inspector shows live messages, event history with filters, controller info grid
- `/metrics`: Prometheus scrape returns valid exposition format
- Event log: every command appears with timestamp and target

### Pause — audit UX, verify observability data quality, test editor on touchscreen

### UI Restructure (2026-10-04) ✅
- [x] 5 tabs → 3 tabs (Dashboard, Music, Settings)
- [x] Modes merged into dashboard inline popover
- [x] Direct merged into dashboard expandable section with real-time color pickers
- [x] LED strip moved to base.html (global, all pages)
- [x] WebSocket ping/pong keepalive + polling fallback (3s when WS unavailable)
- [x] WebSocket 404 fix (route registration order before mounts)
- [x] Removed htmx.min.js (50KB unused)
- [x] Added POST /api/v1/clock/check endpoint

### Bug fixes from Playwright interactive audit (2026-10-04)
- [x] WebSocket 404: added `websockets` pip package (lost when uvicorn[standard] removed)
- [x] Clock sync: /status now includes clock_skew_ms in device field
- [x] All-off: POST /api/v1/off bypasses mode checks (global override)
- [x] Direct Apply: auto-switches to direct mode before sending
- [x] Git songs: DELETE returns 403 for source=git
- [x] Error toasts: API errors show red banner with auto-dismiss

---

## UX Quality Pass — 21 Issues (Playwright audit 2026-10-04) ✅ COMPLETE

### Commit 1 — DOM update refactor (P0) ✅
- [x] Replace innerHTML with targeted DOM updates in app.js (stop animation snap, picker resets, alert reappearance)
- [x] LED off visual distinction (CSS .led-off class for (0,0,0))
- [x] sendDirectAll use actual node LED count (not hardcoded 2)
- [x] toggleNodeDetail triggers immediate re-render
- [x] Direct controls preserve user input between ticks

### Commit 2 — Editor data integrity (P0+P1) ✅
- [x] Fix grid editor save: write _expandedCache edits back to data.beats
- [x] Invalidate cache on addBeat/dupBeat/delBeat
- [x] Replace prompt()/alert() with inline inputs + showToast()
- [x] Add js-yaml CDN or rename Export to JSON

### Commit 3 — Error handling + Settings (P1) ✅
- [x] apiGet error handling + toast
- [x] Settings MQTT setInterval cleared on Pause/Resume
- [x] settings.html triggerClockCheck fix (still calls /songs/stop)
- [x] WS onmessage log parse errors
- [x] Polling fallback error feedback
- [x] Poll events on tick (not just /status)
- [x] initGlobal error state with retry
- [x] Song delete confirmation dialog

### Commit 4 — Accessibility + Contrast (P2) ✅
- [x] WCAG contrast: terminal --muted, midnight --muted
- [x] Missing :active states (.btn.danger, .chip, .nav a, .np-stop)
- [x] ARIA roles (alert, button, tab) + tabindex on interactive elements
- [x] Skip-to-content link

---

## Clickable LED Circles ✅ COMPLETE (2026-10-05)

**Goal:** Replace the Direct Control section with clickable LED circles. Click a circle → inline popover with per-LED color pickers + effect + Apply.

- [x] LED circles clickable (onclick on `.node-unit`)
- [x] Inline popover with color pickers, effect dropdown, Apply
- [x] Auto-switch to direct mode on click (`ensureDirectMode()`)
- [x] Toast feedback for mode switch / music stop
- [x] Click outside / Escape to close
- [x] "Apply to all nodes" in popover
- [x] Popover overflow protection (rightmost node)
- [x] WS updates don't override popover pickers
- [x] Remove Direct Control section (`#direct-section`, `renderDirectControls`, `sendDirect`, etc.)
- [x] Accessibility: role="button", tabindex, keyboard nav, focus trap

### Octolet repo
- No changes needed

---

## Dashboard + Editor Polish (2026-10-04) ✅ COMPLETE

### Dashboard
- [x] WS broadcast shape matches /status API (active_mode, device, cpu_usage fields)
- [x] Health data persists across mode switches (last-known values in direct/music)
- [x] Live LED visualization during music playback (get_current_led_state + 5Hz broadcast)
- [x] Auto clock check on page load
- [x] Presets moved above panels (closer to LED strip)
- [x] Status + Alternate + Stop buttons added to preset bar
- [x] Preset highlight matches active playback (strips preset- prefix)
- [x] All Off reactive danger styling from LED state
- [x] No-device nodes show CPU/mem/disk/clock metrics
- [x] Node table header contrast (uppercase, background, border)
- [x] LED off visual: dashed border + 0.35 opacity
- [x] Renamed Music → Patterns throughout
- [x] Police preset hardcoded red/blue

### Editor
- [x] Fill row (click beat number)
- [x] Fill column (click node header)
- [x] Drag-to-paint (mousedown + mouseover)
- [x] Undo (20-deep stack, Ctrl+Z, button)
- [x] Move beats up/down (↑/↓ per row)
- [x] Clear row (○ per row) + Clear all button
- [x] Swatch indicator (scale + accent ring)
- [x] Grid scroll preservation on mutations
- [x] addBeat scrollIntoView
- [x] Section form inside grid-wrap
- [x] Auto-select first brush on init + loadSong
- [x] Defensive toYaml error handling
- [x] Sticky editor action buttons
- [x] Beat sheet recipe template in CLAUDE.md

---

## Phase 4b: Event Overlays + Creative Modes

**Goal:** Layered overlays, screensaver modes, Twingate integration.

### Engine Foundation ✅ COMPLETE (2026-10-05)
- [x] Mode registry (`MODE_REGISTRY` dict + `register_mode()` factory pattern)
- [x] `set_mode()` refactored: registry lookup + factory with deps dict (replaces if/elif chain)
- [x] `get_available_modes()` iterates registry
- [x] Overlay layer: `_overlay_queue` (priority deque), `_active_overlay` slot
- [x] `trigger_overlay(name, reason, priority, duration, color, effect, params)` — enqueue, preempt, log, metric
- [x] Split LED merge in `_tick()`: LED 0 = background, LED 1 = overlay (suppressed during foreground modes)
- [x] Priority preemption: AlertManager (3) > ArgoCD (2) > Twingate (1); expired overlays promote from queue
- [x] `start_overlay_services()` / `stop_overlay_services()` lifecycle stubs
- [x] WS broadcast includes `active_overlay` + `overlay_reason`
- [x] Config: `LOKI_URL`, `ALERTMANAGER_URL`, `ARGOCD_POLL_INTERVAL`, per-overlay enable flags
- [x] Prometheus: `blinkstick_overlay_triggers_total` counter by name
- [x] `event_log` wired into ModeEngine constructor

### Background modes ✅ COMPLETE (2026-10-05)
- [x] Knight Rider (red scanner sweep with trailing glow, 2s cycle, PHYSICAL_NODE_ORDER)
- [x] Rainbow Wave (traveling hue rotation via colorsys.hsv_to_rgb, 5s cycle)
- [x] Breathing (CPU-proportional sine pulse, green→amber, Prometheus poll)
- [x] Temperature Heatmap (CPU temp → blue/green/yellow/red spectrum, Prometheus poll)

### Service clients ✅ COMPLETE (2026-10-05)
- [x] LokiClient (query, query_range — httpx, graceful degradation)
- [x] AlertManagerClient (get_alerts, get_firing_critical — httpx, graceful degradation)
- [x] K8sClient.list_applications() for ArgoCD Application watch
- [x] Deps dict expanded: loki, alertmanager, k8s wired into ModeEngine

### Event overlays ✅ COMPLETE (2026-10-05)
- [x] Twingate Connection (Loki log query → cyan blink on `established_connection`, dedup by ms timestamp)
- [x] Deploy Wave (ArgoCD Application watch → blue solid on Synced+Healthy transition)
- [x] Alert Escalation (AlertManager API → red blink on critical, re-triggers to hold)

### API + UI ✅ COMPLETE (2026-10-05)
- [x] `GET /api/v1/overlays` — list overlay services with enabled/active state
- [x] `GET /api/v1/overlays/active` — current overlay or null
- [x] Overlay badge in dashboard (color-coded chip: cyan/blue/red, shows name + reason)
- [x] MODE_DESCRIPTIONS for all 7 modes in mode popover

### Polish (deferred to octolet repo)
- [ ] Homepage integration (Ingress annotations in octolet)
- [ ] Optional: Grafana dashboard for agent health

---

## Post-Phase 4 Features ✅ COMPLETE (2026-10-05)
- [x] Network Traffic background mode (blue RX, green TX, logarithmic brightness)
- [x] Notification Flash webhook overlay (POST /api/v1/webhook/flash)
- [x] Pod Lifecycle overlay (green flash on create, amber blink on delete)
- [x] Morse Code blinker foreground mode (POST /api/v1/morse/send)
- [x] Countdown Timer foreground mode (POST /api/v1/timer/start, green→red→flash)

---

## UX Polish ✅ COMPLETE (2026-10-06)

**Goal:** Calm by default, everything opt-in. Restore pre-4b calm while keeping all features accessible.

- [x] Default all 5 overlay configs to OFF (env vars default `"false"`)
- [x] Runtime overlay toggle API (`POST /api/v1/overlays/{name}/toggle`) + dashboard toggle UI
- [x] Pod lifecycle: namespace filtering (`POD_LIFECYCLE_NAMESPACES`), trigger name bug fix
- [x] Legend colors match actual LED output (green/orange/red/slate)
- [x] Dead CSS cleanup (`.led.green/.amber/.red/.blue/.off`, `.no-device-label`, `.np-meta`)
- [x] Overlay badge CSS for pod-lifecycle (green) and webhook (orange)
- [x] Status config persists on engine (`_status_checks`), survives mode switches
- [x] Status config panel always visible (removed mode-gated `hidden`)
- [x] Removed 409 rejection on `POST /status/config` when status mode not active
- [x] Deduplicated `get_music_mode()` in `_do_broadcast`
- [x] Extracted `_compute_severity()` — used by both broadcast and `/status` API
- [x] Visual group labels (Monitoring / Patterns / Control) in action bar
- [x] Distinct Status button styling
- [x] Mode descriptions de-jargoned (removed "Prometheus", "NTP")
- [x] Clock sync thresholds relaxed: ok < 100ms (was 50), warn < 500ms (was 200)
