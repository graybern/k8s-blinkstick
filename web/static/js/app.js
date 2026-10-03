// BlinkStick Panel — WebSocket, LED visualization, API helpers

const API = '/api/v1';

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
    this.statusEl = null;
    this.timeEl = null;
  }

  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    this.ws = new WebSocket(`${proto}://${location.host}/ws/live`);
    this.ws.onopen = () => {
      this.delay = 1000;
      this.setStatus(true);
    };
    this.ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        this.callbacks.forEach(cb => cb(data));
        this.updateTime();
      } catch {}
    };
    this.ws.onclose = () => {
      this.setStatus(false);
      setTimeout(() => this.connect(), this.delay);
      this.delay = Math.min(this.delay * 1.5, 10000);
    };
  }

  on(callback) { this.callbacks.push(callback); }

  setStatus(connected) {
    if (!this.statusEl) this.statusEl = document.querySelector('.ws-dot');
    if (this.statusEl) {
      this.statusEl.className = `ws-dot ${connected ? 'connected' : 'disconnected'}`;
    }
  }

  updateTime() {
    if (!this.timeEl) this.timeEl = document.querySelector('.ws-time');
    if (this.timeEl) this.timeEl.textContent = 'just now';
  }
}

// ── LED rendering ──

const LED_COLORS = {
  healthy: { bg: '#22c55e', shadow: 'rgba(34,197,94,0.5)', outerShadow: 'rgba(34,197,94,0.1)' },
  warning: { bg: '#eab308', shadow: 'rgba(234,179,8,0.5)', outerShadow: 'rgba(234,179,8,0.1)' },
  critical: { bg: '#ef4444', shadow: 'rgba(239,68,68,0.5)', outerShadow: 'rgba(239,68,68,0.1)' },
  offline: { bg: '#6366f1', shadow: 'rgba(99,102,241,0.4)', outerShadow: 'rgba(99,102,241,0.1)' },
};

function ledStyle(r, g, b) {
  const hex = `rgb(${r},${g},${b})`;
  return `background:${hex};box-shadow:0 0 12px 3px rgba(${r},${g},${b},0.5),0 0 32px 8px rgba(${r},${g},${b},0.1)`;
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

function renderPanels(container, data) {
  if (!container) return;
  const mode = data.active_mode || 'unknown';
  const total = data.nodes?.filter(n => n.present).length || 0;
  const healthy = data.nodes?.filter(n => n.health?.severity === 'healthy').length || 0;
  const healthClass = healthy === total ? 'ok' : healthy === 0 ? 'danger' : 'warn';
  const healthDetail = total - healthy > 0 ? `${total - healthy} warning` : 'all healthy';

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
    <div class="panel">
      <div class="panel-label">nodes</div>
      <div class="panel-val ok">${data.nodes?.length || 0}</div>
      <div class="panel-detail">agents discovered</div>
    </div>`;
}

function renderNodes(container, nodes) {
  if (!container || !nodes) return;
  let html = '<div class="section-head"><span class="section-title">Nodes</span></div>';
  html += '<div class="node-table-wrap"><div class="node-header"><span>node</span><span>leds</span><span>cpu</span><span>mem</span><span>disk</span><span>clock</span></div><div class="node-list">';
  nodes.forEach(node => {
    const sev = node.health?.severity || 'unknown';
    const stripe = sev === 'warning' ? ' warn-stripe' : sev === 'critical' ? ' crit-stripe' : '';
    const noDevice = !node.present ? ' no-device' : '';
    const dotClass = sev === 'healthy' ? 'ok' : sev === 'warning' ? 'warn' : sev === 'critical' ? 'crit' : 'off';
    const role = node.name.includes('control') ? 'ctrl' : node.name.includes('worker') ? 'work' : '';

    html += `<div class="node-row${stripe}${noDevice}">`;
    html += `<div class="node-id"><span class="dot ${dotClass}"></span>${node.name}`;
    if (role) html += `<span class="node-role">${role}</span>`;
    html += '</div>';

    if (!node.present) {
      html += '<div class="no-device-label">no blinkstick</div>';
    } else {
      html += '<div class="node-leds-mini">';
      (node.leds || []).forEach(led => {
        const c = led.r > 200 && led.g < 100 ? 'red' : led.g > 200 && led.r < 100 ? 'green' : led.r > 150 && led.g > 150 ? 'amber' : 'off';
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
    }
    html += '</div>';
  });
  html += '</div></div>';
  container.innerHTML = html;
}

function renderNowPlaying(container, playback) {
  if (!container) return;
  if (!playback || !playback.playing) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  const pct = playback.total_beats > 0 ? Math.round((playback.beat_index / playback.total_beats) * 100) : 0;
  const elapsed = Math.round(playback.elapsed || 0);
  const total = playback.total_beats > 0 ? Math.round(playback.total_beats * 0.5) : 0;
  const eMM = String(Math.floor(elapsed / 60));
  const eSS = String(elapsed % 60).padStart(2, '0');
  const tMM = String(Math.floor(total / 60));
  const tSS = String(total % 60).padStart(2, '0');
  container.innerHTML = `
    <div class="np-top">
      <span class="np-song">${playback.song || 'Unknown'}</span>
      <span class="np-spacer"></span>
      <span class="np-time">${eMM}:${eSS} / ${tMM}:${tSS}</span>
      <button class="np-stop" onclick="stopPlayback()">Stop</button>
    </div>
    <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>`;
}

// ── Actions ──

async function switchMode() {
  const modes = await apiGet('/modes');
  const current = modes.find(m => m.active);
  const available = modes.filter(m => !m.active).map(m => m.name);
  if (available.length > 0) {
    await apiPost('/modes/active', { mode: available[0] });
  }
}

async function stopPlayback() {
  await apiPost('/songs/stop');
}

async function playPreset(name) {
  await apiPost(`/presets/${name}/play`, {});
}

// ── Dashboard init ──

async function initDashboard() {
  const data = await apiGet('/status');
  renderLEDStrip(document.getElementById('led-strip'), data.nodes);
  renderPanels(document.getElementById('panels'), data);
  renderNodes(document.getElementById('nodes'), data.nodes);

  const playback = await apiGet('/songs/playing');
  renderNowPlaying(document.getElementById('now-playing'), playback);

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

  const ws = new LiveSocket();
  ws.connect();
  ws.on(data => {
    if (data.nodes) {
      renderLEDStrip(document.getElementById('led-strip'), data.nodes);
      renderPanels(document.getElementById('panels'), data);
      renderNodes(document.getElementById('nodes'), data.nodes);
    }
    if (data.playback !== undefined) {
      renderNowPlaying(document.getElementById('now-playing'), data.playback);
    }
  });

  if (document.getElementById('led-strip')) {
    initDashboard();
  }
});
