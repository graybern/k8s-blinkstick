// BlinkStick Panel — WebSocket, LED visualization, observability

const API = '/api/v1';
let _lastUpdate = 0;
let _expandedNode = null;

// ── API helpers ──

async function apiGet(path) {
  const resp = await fetch(`${API}${path}`);
  return resp.json();
}

async function apiPost(path, body) {
  const resp = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  return resp.json();
}

async function apiDelete(path) {
  const resp = await fetch(`${API}${path}`, { method: 'DELETE' });
  return resp.json();
}

// ── Theme ──

function initTheme() {
  const picker = document.getElementById('theme-picker');
  if (!picker) return;
  let stored = 'system';
  try { stored = localStorage.getItem('blinkstick-theme') || 'system'; } catch {}
  picker.value = stored;
  if (stored !== 'system') document.documentElement.setAttribute('data-theme', stored);
  picker.addEventListener('change', () => {
    const v = picker.value;
    if (v === 'system') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', v);
    try { localStorage.setItem('blinkstick-theme', v); } catch {}
  });
}

// ── WebSocket ──

class LiveSocket {
  constructor() {
    this.ws = null;
    this.delay = 1000;
    this.callbacks = [];
  }

  connect() {
    this._setStatus(null);
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    this.ws = new WebSocket(`${proto}://${location.host}/ws/live`);
    this.ws.onopen = () => {
      this.delay = 1000;
      this._setStatus(true);
    };
    this.ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        _lastUpdate = Date.now();
        this.callbacks.forEach(cb => cb(data));
      } catch {}
    };
    this.ws.onclose = () => {
      this._setStatus(false);
      setTimeout(() => this.connect(), this.delay);
      this.delay = Math.min(this.delay * 1.5, 10000);
    };
  }

  on(callback) { this.callbacks.push(callback); }

  _setStatus(connected) {
    const dot = document.querySelector('.ws-dot');
    const time = document.querySelector('.ws-time');
    if (connected === null) {
      if (dot) dot.className = 'ws-dot disconnected';
      if (time) time.textContent = 'connecting...';
    } else if (connected) {
      if (dot) dot.className = 'ws-dot connected';
      if (time) time.textContent = 'now';
    } else {
      if (dot) dot.className = 'ws-dot disconnected';
      if (time) time.textContent = 'reconnecting...';
    }
  }
}

function updateWsTimer() {
  const el = document.querySelector('.ws-time');
  if (!el || !_lastUpdate) return;
  const sec = Math.round((Date.now() - _lastUpdate) / 1000);
  el.textContent = sec === 0 ? 'now' : `${sec}s ago`;
}

// ── LED rendering ──

function ledStyle(r, g, b) {
  return `background:rgb(${r},${g},${b});box-shadow:0 0 12px 3px rgba(${r},${g},${b},0.5),0 0 32px 8px rgba(${r},${g},${b},0.1)`;
}

function renderLEDStrip(container, nodes) {
  if (!container || !nodes) return;
  let html = '<div class="led-row">';
  nodes.forEach(node => {
    if (!node.present) return;
    html += '<div class="node-unit"><div class="led-pair">';
    (node.leds || []).forEach(led => {
      const breathe = node.health?.severity === 'healthy' ? ' breathe' : '';
      html += `<div class="led${breathe}" style="${ledStyle(led.r, led.g, led.b)}"></div>`;
    });
    if (!node.leds?.length) {
      const n = node.device?.leds || 2;
      for (let i = 0; i < n; i++) html += '<div class="led off"></div>';
    }
    html += `</div><div class="node-label">${node.name.replace('octolet-', '')}</div></div>`;
  });
  html += '</div>';
  html += '<div class="legend">';
  html += '<div class="legend-item"><span class="legend-dot" style="background:#22c55e"></span>healthy</div>';
  html += '<div class="legend-item"><span class="legend-dot" style="background:#eab308"></span>warning</div>';
  html += '<div class="legend-item"><span class="legend-dot" style="background:#ef4444"></span>critical</div>';
  html += '<div class="legend-item"><span class="legend-dot" style="background:#6366f1"></span>offline</div>';
  html += '</div>';
  container.innerHTML = html;
}

// ── Alerts ──

function renderAlerts(container, nodes) {
  if (!container || !nodes) return;
  const alerts = [];
  nodes.forEach(n => {
    if (!n.health || !n.present) return;
    const h = n.health;
    if (h.severity === 'critical') alerts.push({ level: 'crit', text: `${n.name} is critical — ${!h.up ? 'node down' : !h.k8s_ready ? 'not Ready' : 'resource > 90%'}` });
    else if (h.severity === 'warning') {
      const reasons = [];
      if (h.cpu_usage > 0.7) reasons.push(`cpu ${Math.round(h.cpu_usage*100)}%`);
      if (h.memory_usage > 0.7) reasons.push(`mem ${Math.round(h.memory_usage*100)}%`);
      if (h.disk_usage > 0.8) reasons.push(`disk ${Math.round(h.disk_usage*100)}%`);
      alerts.push({ level: 'warn', text: `${n.name} ${reasons.join(', ')} — above threshold` });
    }
  });
  container.innerHTML = alerts.map(a =>
    `<div class="alert-banner ${a.level}"><span class="alert-text">${a.text}</span><button class="alert-dismiss" onclick="this.parentElement.remove()">&times;</button></div>`
  ).join('');
}

// ── Panels (FIXED: clock sync instead of nodes) ──

function renderPanels(container, data) {
  if (!container) return;
  const mode = data.active_mode || 'unknown';
  const present = data.nodes?.filter(n => n.present) || [];
  const total = present.length;
  const healthy = present.filter(n => n.health?.severity === 'healthy').length;
  const healthClass = healthy === total ? 'ok' : healthy === 0 ? 'danger' : 'warn';
  const healthDetail = total - healthy > 0 ? `${total - healthy} warning` : 'all healthy';

  const skews = data.nodes?.map(n => n.device?.clock_skew_ms).filter(s => s != null) || [];
  const maxSkew = skews.length ? Math.round(Math.max(...skews)) : null;
  const syncClass = maxSkew === null ? 'ok' : maxSkew < 50 ? 'ok' : maxSkew < 200 ? 'warn' : 'danger';
  const syncVal = maxSkew !== null ? `<${maxSkew + 1}ms` : '--';
  const syncDetail = maxSkew === null ? 'no data' : maxSkew < 50 ? 'all nodes synced' : `max skew ${maxSkew}ms`;

  container.innerHTML = `
    <div class="panel" onclick="switchMode()">
      <div class="panel-label">mode</div>
      <div class="panel-val ok">${mode}</div>
      <div class="panel-detail">tap to switch</div>
    </div>
    <div class="panel">
      <div class="panel-label">health</div>
      <div class="panel-val ${healthClass}">${healthy} / ${total}</div>
      <div class="panel-detail">${healthDetail}</div>
    </div>
    <div class="panel" onclick="runClockCheck()">
      <div class="panel-label">clock sync</div>
      <div class="panel-val ${syncClass}">${syncVal}</div>
      <div class="panel-detail">${syncDetail === 'no data' ? 'tap to check' : syncDetail}</div>
    </div>`;
}

// ── Nodes (FIXED: clock, roles, expandable detail, warn-stripe) ──

function renderNodes(container, nodes) {
  if (!container || !nodes) return;
  const sec = _lastUpdate ? Math.round((Date.now() - _lastUpdate) / 1000) : '--';
  let html = `<div class="section-head"><span class="section-title">Nodes</span><span class="updated">${nodes.length} agents · updated ${sec}s ago</span></div>`;
  html += '<div class="node-table-wrap"><div class="node-header"><span>node</span><span>leds</span><span>cpu</span><span>mem</span><span>disk</span><span>clock</span></div><div class="node-list">';
  nodes.forEach(node => {
    const sev = node.health?.severity || 'unknown';
    const stripe = sev === 'warning' ? ' warn-stripe' : sev === 'critical' ? ' crit-stripe' : '';
    const noDevice = !node.present ? ' no-device' : '';
    const dotClass = sev === 'healthy' ? 'ok' : sev === 'warning' ? 'warn' : sev === 'critical' ? 'crit' : 'off';
    const role = node.name.includes('control') ? 'ctrl' : node.name.includes('worker') ? 'work' : '';
    const expanded = _expandedNode === node.name;

    html += `<div class="node-row${stripe}${noDevice}" onclick="toggleNodeDetail('${node.name}')">`;
    html += `<div class="node-id"><span class="dot ${dotClass}"></span>${node.name}`;
    if (role) html += `<span class="node-role">${role}</span>`;
    html += '</div>';

    if (!node.present) {
      html += '<div class="no-device-label">no blinkstick</div>';
    } else {
      html += '<div class="node-leds-mini">';
      (node.leds || []).forEach(led => {
        html += `<span class="led-mini" style="background:rgb(${led.r},${led.g},${led.b})"></span>`;
      });
      html += '</div>';
      const h = node.health || {};
      const cpu = h.cpu_usage != null ? `${Math.round(h.cpu_usage * 100)}%` : '--';
      const mem = h.memory_usage != null ? `${Math.round(h.memory_usage * 100)}%` : '--';
      const disk = h.disk_usage != null ? `${Math.round(h.disk_usage * 100)}%` : '--';
      const cpuClass = h.cpu_usage > 0.7 ? ' hot' : '';
      const clock = node.device?.clock_skew_ms != null ? `${Math.round(node.device.clock_skew_ms)}ms` : '--';
      html += `<div class="val${cpuClass}">${cpu}</div><div class="val">${mem}</div><div class="val">${disk}</div><div class="val">${clock}</div>`;

      if (expanded && node.device) {
        const d = node.device;
        const seen = d.last_seen ? `${Math.round((Date.now()/1000 - d.last_seen))}s ago` : '--';
        html += `<div class="node-detail">
          <div class="detail-item">serial<span>${d.serial || '--'}</span></div>
          <div class="detail-item">leds<span>${d.leds} (Nano)</span></div>
          <div class="detail-item">last seen<span>${seen}</span></div>
          <div class="detail-item">clock skew<span>${d.clock_skew_ms != null ? Math.round(d.clock_skew_ms) + 'ms' : '--'}</span></div>
        </div>`;
      }
    }
    html += '</div>';
  });
  html += '</div></div>';
  container.innerHTML = html;
}

function toggleNodeDetail(name) {
  _expandedNode = _expandedNode === name ? null : name;
}

// ── Events ──

const EVENT_ICONS = { mqtt_pub: '→', mqtt_sub: '←', mode_switch: '⟳', play: '▶', stop: '⬛', direct: '→', alert: '!' };
const EVENT_CLASSES = { mqtt_pub: 'pub', mqtt_sub: 'sub', mode_switch: 'mode', play: 'pub', stop: 'alert', direct: 'pub', alert: 'alert' };

function renderEvents(container, events) {
  if (!container) return;
  if (!events || !events.length) { container.innerHTML = '<div class="empty-state">No events yet</div>'; return; }
  let html = events.slice(0, 10).map(e => {
    const t = new Date(e.time * 1000);
    const ts = `${String(t.getHours()).padStart(2,'0')}:${String(t.getMinutes()).padStart(2,'0')}:${String(t.getSeconds()).padStart(2,'0')}`;
    const icon = EVENT_ICONS[e.type] || '·';
    const cls = EVENT_CLASSES[e.type] || 'pub';
    return `<div class="event-item"><div class="event-time">${ts}</div><div class="event-arrow ${cls}">${icon}</div><div class="event-detail">${e.detail || ''}</div><div class="event-target">${e.target || ''}</div></div>`;
  }).join('');
  container.innerHTML = html;
}

// ── Now Playing ──

function renderNowPlaying(container, playback) {
  if (!container) return;
  if (!playback || !playback.playing) { container.hidden = true; return; }
  container.hidden = false;
  const pct = playback.total_beats > 0 ? Math.round((playback.beat_index / playback.total_beats) * 100) : 0;
  const elapsed = Math.round(playback.elapsed || 0);
  const total = playback.total_beats > 0 ? Math.round(playback.total_beats * 0.5) : 0;
  const fmt = s => `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;
  container.innerHTML = `
    <div class="np-top">
      <span class="np-song">${playback.song || 'Unknown'}</span>
      <span class="np-spacer"></span>
      <span class="np-time">${fmt(elapsed)} / ${fmt(total)}</span>
      <button class="np-stop" onclick="stopPlayback()">Stop</button>
    </div>
    <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>`;
}

// ── Actions ──

async function runClockCheck() {
  await apiPost('/songs/stop').catch(() => {});
  const panel = document.querySelector('.panel:last-child .panel-detail');
  if (panel) panel.textContent = 'checking...';
  setTimeout(async () => {
    const data = await apiGet('/status');
    renderPanels(document.getElementById('panels'), data);
  }, 3000);
}

async function switchMode() {
  const modes = await apiGet('/modes');
  const available = modes.filter(m => !m.active).map(m => m.name);
  if (available.length > 0) await apiPost('/modes/active', { mode: available[0] });
}

async function stopPlayback() { await apiPost('/songs/stop'); }

async function playPreset(name) {
  const bpm = parseInt(document.getElementById('preset-bpm')?.value) || 120;
  const color = document.getElementById('preset-color')?.value || '#00ff00';
  const color2 = document.getElementById('preset-color2')?.value || '#0000ff';
  await apiPost(`/presets/${name}/play`, { bpm, color, color2 });
}

// ── Dashboard init ──

async function initDashboard() {
  const data = await apiGet('/status');
  renderAlerts(document.getElementById('alerts'), data.nodes);
  renderLEDStrip(document.getElementById('led-strip'), data.nodes);
  renderPanels(document.getElementById('panels'), data);
  renderNodes(document.getElementById('nodes'), data.nodes);

  const playback = await apiGet('/songs/playing');
  renderNowPlaying(document.getElementById('now-playing'), playback);

  const events = await apiGet('/events?limit=10');
  renderEvents(document.getElementById('events'), events);

  const actions = document.getElementById('actions');
  if (actions) {
    actions.innerHTML = `
      <button class="btn primary" onclick="playPreset('chase')">Chase</button>
      <button class="btn" onclick="playPreset('rainbow')">Rainbow</button>
      <button class="btn" onclick="playPreset('flash')">Flash</button>
      <button class="btn" onclick="playPreset('police')">Police</button>
      <div class="action-sep"></div>
      <button class="btn" onclick="apiPost('/direct',{action:'off'})">All off</button>`;
  }
}

// ── Boot ──

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  setInterval(updateWsTimer, 1000);

  const ws = new LiveSocket();
  ws.connect();
  ws.on(data => {
    if (data.nodes) {
      renderAlerts(document.getElementById('alerts'), data.nodes);
      renderLEDStrip(document.getElementById('led-strip'), data.nodes);
      renderPanels(document.getElementById('panels'), data);
      renderNodes(document.getElementById('nodes'), data.nodes);
    }
    if (data.playback !== undefined) {
      renderNowPlaying(document.getElementById('now-playing'), data.playback);
    }
  });

  if (document.getElementById('led-strip')) initDashboard();
});
