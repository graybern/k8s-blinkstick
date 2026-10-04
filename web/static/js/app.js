// BlinkStick Panel — WebSocket + polling fallback, global LED strip, observability

const API = '/api/v1';
let _lastUpdate = 0;
let _expandedNode = null;
let _lastStatusData = null;
let _wsConnected = false;
let _wsFails = 0;
let _pollInterval = null;

// ── API helpers ──

async function apiGet(path) { return (await fetch(`${API}${path}`)).json(); }
async function apiPost(path, body) {
  const resp = await fetch(`${API}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const data = await resp.json();
  if (!resp.ok) showToast(data.error || `Error ${resp.status}`, 'error');
  return data;
}
async function apiDelete(path) {
  const resp = await fetch(`${API}${path}`, { method: 'DELETE' });
  const data = await resp.json();
  if (!resp.ok) showToast(data.error || `Error ${resp.status}`, 'error');
  return data;
}

function showToast(message, level = 'info') {
  const alerts = document.getElementById('alerts');
  if (!alerts) return;
  const cls = level === 'error' ? 'crit' : 'warn';
  const el = document.createElement('div');
  el.className = `alert-banner ${cls}`;
  el.innerHTML = `<span class="alert-text">${message}</span><button class="alert-dismiss" onclick="this.parentElement.remove()">&times;</button>`;
  alerts.prepend(el);
  setTimeout(() => el.remove(), 5000);
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

// ── WebSocket + polling fallback ──

class LiveSocket {
  constructor() { this.ws = null; this.delay = 1000; this.callbacks = []; this._pingInterval = null; }

  connect() {
    this._setStatus('connecting');
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    try {
      this.ws = new WebSocket(`${proto}://${location.host}/ws/live`);
    } catch { this._onFail(); return; }
    this.ws.onopen = () => {
      this.delay = 1000; _wsConnected = true; _wsFails = 0;
      this._setStatus('connected');
      stopPolling();
      this._pingInterval = setInterval(() => { try { this.ws.send('ping'); } catch {} }, 25000);
    };
    this.ws.onmessage = (e) => {
      if (e.data === 'pong') return;
      try { const data = JSON.parse(e.data); _lastUpdate = Date.now(); this.callbacks.forEach(cb => cb(data)); } catch {}
    };
    this.ws.onclose = () => { this._onFail(); };
    this.ws.onerror = () => {};
  }

  _onFail() {
    _wsConnected = false;
    if (this._pingInterval) { clearInterval(this._pingInterval); this._pingInterval = null; }
    _wsFails++;
    if (_wsFails >= 3) {
      this._setStatus('polling');
      startPolling();
    } else {
      this._setStatus('reconnecting');
      setTimeout(() => this.connect(), this.delay);
      this.delay = Math.min(this.delay * 1.5, 10000);
    }
  }

  on(callback) { this.callbacks.push(callback); }

  _setStatus(state) {
    const dot = document.querySelector('.ws-dot');
    const time = document.querySelector('.ws-time');
    if (state === 'connected') { if (dot) dot.className = 'ws-dot connected'; if (time) time.textContent = 'live'; }
    else if (state === 'polling') { if (dot) dot.className = 'ws-dot connected'; if (time) time.textContent = 'polling'; }
    else if (state === 'connecting') { if (dot) dot.className = 'ws-dot disconnected'; if (time) time.textContent = 'connecting...'; }
    else { if (dot) dot.className = 'ws-dot disconnected'; if (time) time.textContent = 'reconnecting...'; }
  }
}

function startPolling() {
  if (_pollInterval) return;
  _pollInterval = setInterval(async () => {
    try {
      const data = await apiGet('/status');
      _lastUpdate = Date.now();
      _lastStatusData = data;
      updateAllViews(data);
    } catch {}
  }, 3000);
}

function stopPolling() {
  if (_pollInterval) { clearInterval(_pollInterval); _pollInterval = null; }
}

function updateWsTimer() {
  if (_wsConnected || _pollInterval) return;
  const el = document.querySelector('.ws-time');
  if (!el || !_lastUpdate) return;
  const sec = Math.round((Date.now() - _lastUpdate) / 1000);
  el.textContent = sec === 0 ? 'live' : `${sec}s ago`;
}

// ── Global update dispatcher ──

function updateAllViews(data) {
  if (data.nodes) {
    renderLEDStrip(document.getElementById('led-strip'), data.nodes);
    renderAlerts(document.getElementById('alerts'), data.nodes);
    renderPanels(document.getElementById('panels'), data);
    renderNodes(document.getElementById('nodes'), data.nodes);
    updateDirectColors(data.nodes);
  }
  if (data.playback !== undefined) renderNowPlaying(document.getElementById('now-playing'), data.playback);
}

// ── LED rendering (global — renders on every page) ──

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
    if (!node.leds?.length) { const n = node.device?.leds || 2; for (let i = 0; i < n; i++) html += '<div class="led off"></div>'; }
    html += `</div><div class="node-label">${node.name.replace('octolet-', '')}</div></div>`;
  });
  html += '</div><div class="legend">';
  html += '<div class="legend-item"><span class="legend-dot" style="background:#22c55e"></span>healthy</div>';
  html += '<div class="legend-item"><span class="legend-dot" style="background:#eab308"></span>warning</div>';
  html += '<div class="legend-item"><span class="legend-dot" style="background:#ef4444"></span>critical</div>';
  html += '<div class="legend-item"><span class="legend-dot" style="background:#6366f1"></span>offline</div></div>';
  container.innerHTML = html;
}

// ── Alerts ──

function renderAlerts(container, nodes) {
  if (!container || !nodes) return;
  const alerts = [];
  nodes.forEach(n => {
    if (!n.health || !n.present) return;
    const h = n.health;
    if (h.severity === 'critical') alerts.push({ level: 'crit', text: `${n.name} is critical` });
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

// ── Panels ──

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
  const syncDetail = maxSkew === null ? 'tap to check' : maxSkew < 50 ? 'all synced' : `max ${maxSkew}ms`;

  container.innerHTML = `
    <div class="panel" onclick="toggleModePopover()">
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
      <div class="panel-detail">${syncDetail}</div>
    </div>`;

  updateDirectSection(mode, data.nodes);
}

// ── Mode popover ──

const MODE_DESCRIPTIONS = {
  status: 'Prometheus health → green breathing, amber warning, red critical',
  direct: 'Manual per-node LED control with color pickers',
  music: 'Synchronized beat sheet playback via NTP clock sync',
};

async function toggleModePopover() {
  const popover = document.getElementById('mode-popover');
  if (!popover) return;
  if (!popover.hidden) { popover.hidden = true; return; }
  const modes = await apiGet('/modes');
  document.getElementById('mode-cards').innerHTML = modes.map(m => `
    <div class="mode-card${m.active ? ' active' : ''}">
      <div style="display:flex;align-items:center;gap:10px">
        <div style="flex:1">
          <div class="mode-name">${m.name}</div>
          <div style="font-size:11px;color:var(--dim);margin-top:2px">${MODE_DESCRIPTIONS[m.name] || m.layer}</div>
        </div>
        ${m.active ? '<span style="font-size:11px;color:var(--accent);font-weight:500">Active</span>'
          : `<button class="btn" onclick="setMode('${m.name}')">Switch</button>`}
      </div>
    </div>`).join('');
  popover.hidden = false;
}

async function setMode(name) {
  await apiPost('/modes/active', { mode: name });
  document.getElementById('mode-popover').hidden = true;
  const data = await apiGet('/status');
  _lastStatusData = data;
  updateAllViews(data);
}

// ── Direct controls (inline on dashboard) ──

function updateDirectSection(mode, nodes) {
  const section = document.getElementById('direct-section');
  if (!section) return;
  const hint = document.getElementById('direct-mode-hint');
  if (mode === 'direct') {
    section.hidden = false;
    if (hint) hint.textContent = '';
    renderDirectControls(nodes);
  } else {
    section.hidden = false;
    if (hint) hint.innerHTML = `<button class="btn" style="height:28px;font-size:10px" onclick="setMode('direct')">Enable direct mode</button>`;
    renderDirectControls(nodes);
  }
}

function renderDirectControls(nodes) {
  const el = document.getElementById('direct-controls');
  if (!el || !nodes) return;
  const presentNodes = nodes.filter(n => n.present);
  if (!presentNodes.length) { el.innerHTML = '<div class="empty-state">No devices online</div>'; return; }

  let html = `<div class="song-item" style="background:var(--accent-dim);border-color:rgba(34,197,94,0.2)">
    <span class="song-name" style="color:var(--accent)">All nodes</span>
    <input type="color" id="color-all" value="#00ff00" style="min-width:44px">
    <select id="effect-all" style="width:auto;min-width:80px;min-height:44px">
      <option value="solid">solid</option><option value="pulse">pulse</option><option value="blink">blink</option><option value="morph">morph</option>
    </select>
    <button class="btn primary" onclick="sendDirectAll()">Apply all</button>
  </div>`;

  presentNodes.forEach(n => {
    const currentHex = nodeToHex(n);
    html += `<div class="song-item">
      <span class="song-name">${n.name.replace('octolet-','')}</span>
      <input type="color" id="color-${n.name}" value="${currentHex}" style="min-width:44px">
      <select id="effect-${n.name}" style="width:auto;min-width:80px;min-height:44px">
        <option value="solid">solid</option><option value="pulse">pulse</option><option value="blink">blink</option><option value="morph">morph</option>
      </select>
      <button class="btn" onclick="sendDirect('${n.name}',${n.device?.leds||2})">Apply</button>
    </div>`;
  });
  el.innerHTML = html;
}

function nodeToHex(node) {
  const led = node.leds?.[0];
  if (!led) return '#00ff00';
  return '#' + [led.r, led.g, led.b].map(c => c.toString(16).padStart(2,'0')).join('');
}

function updateDirectColors(nodes) {
  if (!nodes) return;
  nodes.filter(n => n.present).forEach(n => {
    const input = document.getElementById(`color-${n.name}`);
    if (input && document.activeElement !== input) input.value = nodeToHex(n);
  });
}

function hexToRgb(hex) { return { r: parseInt(hex.slice(1,3),16), g: parseInt(hex.slice(3,5),16), b: parseInt(hex.slice(5,7),16) }; }

async function ensureDirectMode() {
  const mode = _lastStatusData?.active_mode;
  if (mode !== 'direct') await apiPost('/modes/active', { mode: 'direct' });
}

async function sendDirect(node, ledCount) {
  await ensureDirectMode();
  const {r,g,b} = hexToRgb(document.getElementById(`color-${node}`).value);
  const effect = document.getElementById(`effect-${node}`).value;
  const leds = []; for (let i = 0; i < ledCount; i++) leds.push({ index: i, r, g, b });
  await apiPost('/direct', { action: 'set', leds, effect, params: {}, node });
}

async function sendDirectAll() {
  await ensureDirectMode();
  const {r,g,b} = hexToRgb(document.getElementById('color-all').value);
  const effect = document.getElementById('effect-all').value;
  await apiPost('/direct', { action: 'set', leds: [{ index: 0, r, g, b }, { index: 1, r, g, b }], effect, params: {} });
}

// ── Nodes ──

function renderNodes(container, nodes) {
  if (!container || !nodes) return;
  const sec = _lastUpdate ? Math.round((Date.now() - _lastUpdate) / 1000) : '--';
  let html = `<div class="section-head"><span class="section-title">Nodes</span><span class="updated">${nodes.length} agents · ${sec}s ago</span></div>`;
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
    if (!node.present) { html += '<div class="no-device-label">no blinkstick</div>'; }
    else {
      html += '<div class="node-leds-mini">';
      (node.leds || []).forEach(led => { html += `<span class="led-mini" style="background:rgb(${led.r},${led.g},${led.b})"></span>`; });
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
        html += `<div class="node-detail"><div class="detail-item">serial<span>${d.serial||'--'}</span></div><div class="detail-item">leds<span>${d.leds}</span></div><div class="detail-item">last seen<span>${seen}</span></div><div class="detail-item">clock<span>${d.clock_skew_ms!=null?Math.round(d.clock_skew_ms)+'ms':'--'}</span></div></div>`;
      }
    }
    html += '</div>';
  });
  html += '</div></div>';
  container.innerHTML = html;
}

function toggleNodeDetail(name) { _expandedNode = _expandedNode === name ? null : name; }

// ── Events ──

const EVENT_ICONS = { mqtt_pub: '→', mode_switch: '⟳', play: '▶', stop: '⬛', direct: '→', alert: '!' };
const EVENT_CLASSES = { mqtt_pub: 'pub', mode_switch: 'mode', play: 'pub', stop: 'alert', direct: 'pub', alert: 'alert' };

function renderEvents(container, events) {
  if (!container) return;
  if (!events?.length) { container.innerHTML = '<div class="empty-state">No events yet</div>'; return; }
  container.innerHTML = events.slice(0, 10).map(e => {
    const t = new Date(e.time * 1000);
    const ts = `${String(t.getHours()).padStart(2,'0')}:${String(t.getMinutes()).padStart(2,'0')}:${String(t.getSeconds()).padStart(2,'0')}`;
    return `<div class="event-item"><div class="event-time">${ts}</div><div class="event-arrow ${EVENT_CLASSES[e.type]||'pub'}">${EVENT_ICONS[e.type]||'·'}</div><div class="event-detail">${e.detail||''}</div><div class="event-target">${e.target||''}</div></div>`;
  }).join('');
}

// ── Now Playing ──

function renderNowPlaying(container, playback) {
  if (!container) return;
  if (!playback?.playing) { container.hidden = true; return; }
  container.hidden = false;
  const pct = playback.total_beats > 0 ? Math.round((playback.beat_index / playback.total_beats) * 100) : 0;
  const elapsed = Math.round(playback.elapsed || 0);
  const total = playback.total_beats > 0 ? Math.round(playback.total_beats * 0.5) : 0;
  const fmt = s => `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;
  container.innerHTML = `<div class="np-top"><span class="np-song">${playback.song||'Unknown'}</span><span class="np-spacer"></span><span class="np-time">${fmt(elapsed)} / ${fmt(total)}</span><button class="np-stop" onclick="stopPlayback()">Stop</button></div><div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>`;
}

// ── Actions ──

async function runClockCheck() {
  const panel = document.querySelector('.panel:last-child .panel-detail');
  if (panel) panel.textContent = 'checking...';
  await apiPost('/clock/check').catch(() => {});
  setTimeout(async () => { const data = await apiGet('/status'); _lastStatusData = data; updateAllViews(data); }, 3000);
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
  _lastStatusData = data;
  _lastUpdate = Date.now();
  updateAllViews(data);

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
      <button class="btn" onclick="apiPost('/off')">All off</button>`;
  }
}

// ── Global init (runs on every page) ──

async function initGlobal() {
  try {
    const data = await apiGet('/status');
    _lastStatusData = data;
    _lastUpdate = Date.now();
    renderLEDStrip(document.getElementById('led-strip'), data.nodes);
    renderAlerts(document.getElementById('alerts'), data.nodes);
  } catch {}
}

// ── Boot ──

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  setInterval(updateWsTimer, 1000);

  const ws = new LiveSocket();
  ws.connect();
  ws.on(data => updateAllViews(data));

  initGlobal();
  if (document.getElementById('panels')) initDashboard();
});
