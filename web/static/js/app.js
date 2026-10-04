// BlinkStick Panel — WebSocket + polling fallback, global LED strip, observability

const API = '/api/v1';
let _lastUpdate = 0;
let _expandedNode = null;
let _lastStatusData = null;
let _wsConnected = false;
let _wsFails = 0;
let _pollInterval = null;
let _lastEventFetch = 0;
const _dismissedAlerts = new Set();

// ── API helpers ──

async function apiGet(path) {
  try {
    const resp = await fetch(`${API}${path}`);
    if (!resp.ok) { showToast(`Error ${resp.status}: ${path}`, 'error'); return null; }
    return await resp.json();
  } catch { showToast(`Network error: ${path}`, 'error'); return null; }
}
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
      try { const data = JSON.parse(e.data); _lastUpdate = Date.now(); this.callbacks.forEach(cb => cb(data)); }
      catch (err) { console.warn('WS parse error:', err.message); }
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
      if (!data) return;
      _lastUpdate = Date.now();
      _lastStatusData = data;
      updateAllViews(data);
    } catch (err) { console.warn('Poll error:', err.message); }
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

  const eventsEl = document.getElementById('events');
  if (eventsEl && Date.now() - _lastEventFetch > 3000) {
    _lastEventFetch = Date.now();
    apiGet('/events?limit=10').then(events => { if (events) renderEvents(eventsEl, events); });
  }
}

// ── LED rendering (creates once, updates styles in place — no animation snap) ──

function renderLEDStrip(container, nodes) {
  if (!container || !nodes) return;
  const presentNodes = nodes.filter(n => n.present);

  if (!container._ledBuilt) {
    container.innerHTML = '';
    const row = document.createElement('div');
    row.className = 'led-row';
    container.appendChild(row);
    const legend = document.createElement('div');
    legend.className = 'legend';
    legend.innerHTML =
      '<div class="legend-item"><span class="legend-dot" style="background:#22c55e"></span>healthy</div>' +
      '<div class="legend-item"><span class="legend-dot" style="background:#eab308"></span>warning</div>' +
      '<div class="legend-item"><span class="legend-dot" style="background:#ef4444"></span>critical</div>' +
      '<div class="legend-item"><span class="legend-dot" style="background:#6366f1"></span>offline</div>';
    container.appendChild(legend);
    container._ledBuilt = true;
    container._ledNodeKey = '';
  }

  const row = container.querySelector('.led-row');
  const nodeKey = presentNodes.map(n => `${n.name}:${n.leds?.length || n.device?.leds || 2}`).join(',');

  if (nodeKey !== container._ledNodeKey) {
    row.innerHTML = '';
    presentNodes.forEach(node => {
      const unit = document.createElement('div');
      unit.className = 'node-unit';
      unit.dataset.node = node.name;
      const pair = document.createElement('div');
      pair.className = 'led-pair';
      const ledCount = node.leds?.length || node.device?.leds || 2;
      for (let i = 0; i < ledCount; i++) {
        const led = document.createElement('div');
        led.className = 'led led-off';
        pair.appendChild(led);
      }
      unit.appendChild(pair);
      const label = document.createElement('div');
      label.className = 'node-label';
      label.textContent = node.name.replace('octolet-', '');
      unit.appendChild(label);
      row.appendChild(unit);
    });
    container._ledNodeKey = nodeKey;
  }

  presentNodes.forEach(node => {
    const unit = row.querySelector(`[data-node="${node.name}"]`);
    if (!unit) return;
    const ledEls = unit.querySelectorAll('.led');
    const ledData = node.leds || [];

    ledEls.forEach((el, i) => {
      const led = ledData[i];
      if (!led) {
        el.style.background = '';
        el.style.boxShadow = '';
        el.classList.remove('breathe');
        el.classList.add('led-off');
        return;
      }
      const isOff = led.r === 0 && led.g === 0 && led.b === 0;
      const shouldBreathe = !isOff && node.health?.severity === 'healthy';
      el.style.background = isOff ? '' : `rgb(${led.r},${led.g},${led.b})`;
      el.style.boxShadow = isOff ? '' : `0 0 12px 3px rgba(${led.r},${led.g},${led.b},0.5),0 0 32px 8px rgba(${led.r},${led.g},${led.b},0.1)`;
      el.classList.toggle('breathe', shouldBreathe);
      el.classList.toggle('led-off', isOff);
    });
  });
}

// ── Alerts (tracks dismissed IDs — dismissed alerts stay gone until condition clears) ──

function dismissAlert(btn) {
  const banner = btn.parentElement;
  if (banner.dataset.alertId) _dismissedAlerts.add(banner.dataset.alertId);
  banner.remove();
}

function renderAlerts(container, nodes) {
  if (!container || !nodes) return;

  const current = new Map();
  nodes.forEach(n => {
    if (!n.health || !n.present) return;
    const h = n.health;
    if (h.severity === 'critical') {
      current.set(`${n.name}-crit`, { level: 'crit', text: `${n.name} is critical` });
    } else if (h.severity === 'warning') {
      const reasons = [];
      if (h.cpu_usage > 0.7) reasons.push(`cpu ${Math.round(h.cpu_usage*100)}%`);
      if (h.memory_usage > 0.7) reasons.push(`mem ${Math.round(h.memory_usage*100)}%`);
      if (h.disk_usage > 0.8) reasons.push(`disk ${Math.round(h.disk_usage*100)}%`);
      current.set(`${n.name}-warn`, { level: 'warn', text: `${n.name} ${reasons.join(', ')} — above threshold` });
    }
  });

  container.querySelectorAll('[data-alert-id]').forEach(el => {
    if (!current.has(el.dataset.alertId)) el.remove();
  });
  _dismissedAlerts.forEach(id => { if (!current.has(id)) _dismissedAlerts.delete(id); });

  current.forEach((alert, id) => {
    if (_dismissedAlerts.has(id)) return;
    if (container.querySelector(`[data-alert-id="${id}"]`)) return;
    const el = document.createElement('div');
    el.className = `alert-banner ${alert.level}`;
    el.dataset.alertId = id;
    el.innerHTML = `<span class="alert-text">${alert.text}</span><button class="alert-dismiss" onclick="dismissAlert(this)">&times;</button>`;
    container.appendChild(el);
  });
}

// ── Panels (creates structure once, updates text/classes in place) ──

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

  if (!container._panelsBuilt) {
    container.innerHTML = `
      <div class="panel" onclick="toggleModePopover()" data-panel="mode" role="button" tabindex="0" aria-label="Switch mode">
        <div class="panel-label">mode</div>
        <div class="panel-val ok" data-role="val"></div>
        <div class="panel-detail" data-role="detail">tap to switch</div>
      </div>
      <div class="panel" data-panel="health">
        <div class="panel-label">health</div>
        <div class="panel-val" data-role="val"></div>
        <div class="panel-detail" data-role="detail"></div>
      </div>
      <div class="panel" onclick="runClockCheck()" data-panel="sync" role="button" tabindex="0" aria-label="Check clock sync">
        <div class="panel-label">clock sync</div>
        <div class="panel-val" data-role="val"></div>
        <div class="panel-detail" data-role="detail"></div>
      </div>`;
    container._panelsBuilt = true;
  }

  const modePanel = container.querySelector('[data-panel="mode"]');
  const healthPanel = container.querySelector('[data-panel="health"]');
  const syncPanel = container.querySelector('[data-panel="sync"]');

  const mv = modePanel.querySelector('[data-role="val"]');
  mv.textContent = mode;
  mv.className = 'panel-val ok';

  const hv = healthPanel.querySelector('[data-role="val"]');
  hv.textContent = `${healthy} / ${total}`;
  hv.className = `panel-val ${healthClass}`;
  healthPanel.querySelector('[data-role="detail"]').textContent = healthDetail;

  const sv = syncPanel.querySelector('[data-role="val"]');
  sv.textContent = syncVal;
  sv.className = `panel-val ${syncClass}`;
  syncPanel.querySelector('[data-role="detail"]').textContent = syncDetail;

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
  if (!modes) return;
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
  if (!data) return;
  _lastStatusData = data;
  updateAllViews(data);
}

// ── Direct controls (only rebuilds when node list changes — preserves picker state) ──

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
  if (!presentNodes.length) {
    el.innerHTML = '<div class="empty-state">No devices online</div>';
    el._dcNodeKey = '';
    return;
  }

  const nodeKey = presentNodes.map(n => n.name).join(',');
  if (nodeKey === el._dcNodeKey) return;

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
  el._dcNodeKey = nodeKey;
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
  const maxLeds = Math.max(2, ...(_lastStatusData?.nodes || []).filter(n => n.present).map(n => n.device?.leds || n.leds?.length || 2));
  const leds = [];
  for (let i = 0; i < maxLeds; i++) leds.push({ index: i, r, g, b });
  await apiPost('/direct', { action: 'set', leds, effect, params: {} });
}

// ── Nodes (creates rows once per topology, updates cells in place) ──

function renderNodes(container, nodes) {
  if (!container || !nodes) return;

  if (!container._nodesBuilt) {
    container.innerHTML = `
      <div class="section-head">
        <span class="section-title">Nodes</span>
        <span class="updated" data-role="updated"></span>
      </div>
      <div class="node-table-wrap">
        <div class="node-header"><span>node</span><span>leds</span><span>cpu</span><span>mem</span><span>disk</span><span>clock</span></div>
        <div class="node-list" data-role="node-list"></div>
      </div>`;
    container._nodesBuilt = true;
    container._nodeTopology = '';
  }

  const sec = _lastUpdate ? Math.round((Date.now() - _lastUpdate) / 1000) : '--';
  container.querySelector('[data-role="updated"]').textContent = `${nodes.length} agents · ${sec}s ago`;

  const list = container.querySelector('[data-role="node-list"]');
  const topoKey = nodes.map(n => `${n.name}:${n.present ? 1 : 0}:${n.leds?.length || 0}`).join(',');

  if (topoKey !== container._nodeTopology) {
    list.innerHTML = '';
    nodes.forEach(node => {
      const row = document.createElement('div');
      row.dataset.node = node.name;
      row.setAttribute('role', 'button');
      row.tabIndex = 0;
      row.addEventListener('click', () => toggleNodeDetail(node.name));

      const id = document.createElement('div');
      id.className = 'node-id';
      const dot = document.createElement('span');
      dot.className = 'dot off';
      dot.dataset.role = 'dot';
      id.appendChild(dot);
      id.appendChild(document.createTextNode(node.name));
      const roleName = node.name.includes('control') ? 'ctrl' : node.name.includes('worker') ? 'work' : '';
      if (roleName) {
        const roleSpan = document.createElement('span');
        roleSpan.className = 'node-role';
        roleSpan.textContent = roleName;
        id.appendChild(roleSpan);
      }
      row.appendChild(id);

      if (!node.present) {
        row.className = 'node-row no-device';
        const noLabel = document.createElement('div');
        noLabel.className = 'no-device-label';
        noLabel.textContent = 'no blinkstick';
        row.appendChild(noLabel);
      } else {
        row.className = 'node-row';
        const ledsMini = document.createElement('div');
        ledsMini.className = 'node-leds-mini';
        ledsMini.dataset.role = 'leds-mini';
        const ledCount = node.leds?.length || node.device?.leds || 2;
        for (let i = 0; i < ledCount; i++) {
          const mini = document.createElement('span');
          mini.className = 'led-mini';
          ledsMini.appendChild(mini);
        }
        row.appendChild(ledsMini);

        ['cpu', 'mem', 'disk', 'clock'].forEach(name => {
          const cell = document.createElement('div');
          cell.className = 'val';
          cell.dataset.role = name;
          cell.textContent = '--';
          row.appendChild(cell);
        });

        const detail = document.createElement('div');
        detail.className = 'node-detail';
        detail.dataset.role = 'detail';
        detail.style.display = 'none';
        ['serial', 'leds', 'last seen', 'clock'].forEach(label => {
          const item = document.createElement('div');
          item.className = 'detail-item';
          item.appendChild(document.createTextNode(label));
          const val = document.createElement('span');
          val.textContent = '--';
          item.appendChild(val);
          detail.appendChild(item);
        });
        row.appendChild(detail);
      }

      list.appendChild(row);
    });
    container._nodeTopology = topoKey;
  }

  nodes.forEach(node => {
    const row = list.querySelector(`[data-node="${node.name}"]`);
    if (!row) return;

    const sev = node.health?.severity || 'unknown';
    const stripe = sev === 'warning' ? ' warn-stripe' : sev === 'critical' ? ' crit-stripe' : '';
    const noDevice = !node.present ? ' no-device' : '';
    row.className = `node-row${stripe}${noDevice}`;

    const dotEl = row.querySelector('[data-role="dot"]');
    if (dotEl) {
      const dotClass = sev === 'healthy' ? 'ok' : sev === 'warning' ? 'warn' : sev === 'critical' ? 'crit' : 'off';
      dotEl.className = `dot ${dotClass}`;
    }

    if (!node.present) return;

    const ledsMini = row.querySelector('[data-role="leds-mini"]');
    if (ledsMini) {
      const minis = ledsMini.querySelectorAll('.led-mini');
      (node.leds || []).forEach((led, i) => {
        if (i < minis.length) minis[i].style.background = `rgb(${led.r},${led.g},${led.b})`;
      });
    }

    const h = node.health || {};
    const cpuCell = row.querySelector('[data-role="cpu"]');
    if (cpuCell) {
      cpuCell.textContent = h.cpu_usage != null ? `${Math.round(h.cpu_usage * 100)}%` : '--';
      cpuCell.className = h.cpu_usage > 0.7 ? 'val hot' : 'val';
    }
    const memCell = row.querySelector('[data-role="mem"]');
    if (memCell) memCell.textContent = h.memory_usage != null ? `${Math.round(h.memory_usage * 100)}%` : '--';
    const diskCell = row.querySelector('[data-role="disk"]');
    if (diskCell) diskCell.textContent = h.disk_usage != null ? `${Math.round(h.disk_usage * 100)}%` : '--';
    const clockCell = row.querySelector('[data-role="clock"]');
    if (clockCell) clockCell.textContent = node.device?.clock_skew_ms != null ? `${Math.round(node.device.clock_skew_ms)}ms` : '--';

    const detail = row.querySelector('[data-role="detail"]');
    if (detail) {
      const expanded = _expandedNode === node.name;
      detail.style.display = expanded ? '' : 'none';
      if (expanded && node.device) {
        const spans = detail.querySelectorAll('.detail-item span');
        const d = node.device;
        if (spans[0]) spans[0].textContent = d.serial || '--';
        if (spans[1]) spans[1].textContent = d.leds != null ? String(d.leds) : '--';
        if (spans[2]) spans[2].textContent = d.last_seen ? `${Math.round(Date.now()/1000 - d.last_seen)}s ago` : '--';
        if (spans[3]) spans[3].textContent = d.clock_skew_ms != null ? `${Math.round(d.clock_skew_ms)}ms` : '--';
      }
    }
  });
}

function toggleNodeDetail(name) {
  _expandedNode = _expandedNode === name ? null : name;
  renderNodes(document.getElementById('nodes'), _lastStatusData?.nodes);
}

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

// ── Now Playing (creates structure once, updates text + progress in place) ──

function renderNowPlaying(container, playback) {
  if (!container) return;
  if (!playback?.playing) {
    container.hidden = true;
    container._npBuilt = false;
    return;
  }
  container.hidden = false;

  const pct = playback.total_beats > 0 ? Math.round((playback.beat_index / playback.total_beats) * 100) : 0;
  const elapsed = Math.round(playback.elapsed || 0);
  const total = playback.total_beats > 0 ? Math.round(playback.total_beats * 0.5) : 0;
  const fmt = s => `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;

  if (!container._npBuilt) {
    container.innerHTML = `
      <div class="np-top">
        <span class="np-song"></span>
        <span class="np-spacer"></span>
        <span class="np-time"></span>
        <button class="np-stop" onclick="stopPlayback()">Stop</button>
      </div>
      <div class="progress-bar"><div class="progress-fill"></div></div>`;
    container._npBuilt = true;
  }

  container.querySelector('.np-song').textContent = playback.song || 'Unknown';
  container.querySelector('.np-time').textContent = `${fmt(elapsed)} / ${fmt(total)}`;
  container.querySelector('.progress-fill').style.width = `${pct}%`;
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
  if (data) {
    _lastStatusData = data;
    _lastUpdate = Date.now();
    updateAllViews(data);
  }

  const playback = await apiGet('/songs/playing');
  if (playback) renderNowPlaying(document.getElementById('now-playing'), playback);

  const events = await apiGet('/events?limit=10');
  if (events) renderEvents(document.getElementById('events'), events);

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
    if (!data) throw new Error('No data');
    _lastStatusData = data;
    _lastUpdate = Date.now();
    renderLEDStrip(document.getElementById('led-strip'), data.nodes);
    renderAlerts(document.getElementById('alerts'), data.nodes);
  } catch {
    const strip = document.getElementById('led-strip');
    if (strip) strip.innerHTML = '<div class="empty-state">Connection lost <button class="btn" style="margin-left:8px" onclick="initGlobal()">Retry</button></div>';
  }
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
