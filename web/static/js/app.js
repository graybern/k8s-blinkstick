// BlinkStick Panel — WebSocket + polling fallback, global LED strip, observability

const API = '/api/v1';
let _lastUpdate = 0;
let _expandedNode = null;
let _lastStatusData = null;
let _wsConnected = false;
let _wsFails = 0;
let _pollInterval = null;
let _lastEventFetch = 0;
let _activePopoverNode = null;
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
  if (!data.active_mode && data.mode) data.active_mode = data.mode;
  _lastStatusData = data;
  if (data.nodes) {
    renderLEDStrip(document.getElementById('led-strip'), data.nodes);
    renderAlerts(document.getElementById('alerts'), data.nodes);
    renderPanels(document.getElementById('panels'), data);
    renderNodes(document.getElementById('nodes'), data.nodes);
    updateAllOffButton(data.nodes);
  }
  if (data.playback !== undefined) {
    renderNowPlaying(document.getElementById('now-playing'), data.playback);
    updatePresetButtons(data.playback);
  }
  renderOverlayBadge(document.getElementById('overlay-badge'), data.active_overlay, data.overlay_reason);

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
      unit.setAttribute('role', 'button');
      unit.tabIndex = 0;
      unit.setAttribute('aria-label', `Control LEDs on ${node.name.replace('octolet-', '')}`);
      unit.addEventListener('click', (e) => {
        if (e.target.closest('.led-popover')) return;
        openLedPopover(node.name);
      });
      unit.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openLedPopover(node.name); }
      });
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

// ── LED popover (click LED circle → inline color picker + effect control) ──

function openLedPopover(nodeName) {
  const wasOpen = _activePopoverNode === nodeName;
  closeLedPopover();
  if (wasOpen) return;

  _activePopoverNode = nodeName;
  const unit = document.querySelector(`.node-unit[data-node="${nodeName}"]`);
  if (!unit) return;
  unit.classList.add('active');

  const node = _lastStatusData?.nodes?.find(n => n.name === nodeName);
  if (!node?.present) return;

  const ledCount = node.leds?.length || node.device?.leds || 2;
  const popover = document.createElement('div');
  popover.className = 'led-popover';
  popover.setAttribute('role', 'dialog');
  popover.setAttribute('aria-label', `Control LEDs on ${nodeName.replace('octolet-', '')}`);

  let html = `<div class="pop-header"><span class="pop-title">${nodeName.replace('octolet-', '')}</span><button class="pop-close" onclick="closeLedPopover()" aria-label="Close">&times;</button></div>`;
  for (let i = 0; i < ledCount; i++) {
    const led = node.leds?.[i];
    const hex = led ? '#' + [led.r, led.g, led.b].map(c => c.toString(16).padStart(2, '0')).join('') : '#000000';
    html += `<div class="pop-row"><label>LED ${i}</label><input type="color" id="pop-led-${i}" value="${hex}"></div>`;
  }
  html += `<div class="pop-row"><label>Effect</label><select id="pop-effect"><option value="solid">solid</option><option value="pulse">pulse</option><option value="blink">blink</option><option value="morph">morph</option></select></div>`;
  html += `<div class="pop-actions"><button class="btn primary" onclick="applyLedPopover('${nodeName}',${ledCount})">Apply</button><button class="btn" onclick="applyLedPopoverAll(${ledCount})">All nodes</button></div>`;
  popover.innerHTML = html;
  unit.appendChild(popover);

  requestAnimationFrame(() => {
    const rect = popover.getBoundingClientRect();
    if (rect.right > window.innerWidth - 16) {
      popover.style.left = 'auto';
      popover.style.right = '0';
      popover.style.transform = 'none';
    }
    if (rect.left < 16) {
      popover.style.left = '0';
      popover.style.transform = 'none';
    }
  });

  popover.querySelector('input[type="color"]')?.focus();
}

function closeLedPopover() {
  const existing = document.querySelector('.led-popover');
  if (existing) existing.remove();
  document.querySelectorAll('.node-unit.active').forEach(u => u.classList.remove('active'));
  _activePopoverNode = null;
}

function hexToRgb(hex) { return { r: parseInt(hex.slice(1,3),16), g: parseInt(hex.slice(3,5),16), b: parseInt(hex.slice(5,7),16) }; }

async function ensureDirectMode() {
  const mode = _lastStatusData?.active_mode;
  if (mode === 'direct') return;
  const wasMusic = mode === 'music';
  await apiPost('/modes/active', { mode: 'direct' });
  showToast(wasMusic ? 'Stopped playback — switched to direct mode' : 'Switched to direct mode');
}

async function applyLedPopover(nodeName, ledCount) {
  await ensureDirectMode();
  const leds = [];
  for (let i = 0; i < ledCount; i++) {
    const hex = document.getElementById(`pop-led-${i}`)?.value || '#000000';
    const {r, g, b} = hexToRgb(hex);
    leds.push({ index: i, r, g, b });
  }
  const effect = document.getElementById('pop-effect')?.value || 'solid';
  await apiPost('/direct', { action: 'set', leds, effect, params: {}, node: nodeName });
}

async function applyLedPopoverAll(ledCount) {
  await ensureDirectMode();
  const leds = [];
  for (let i = 0; i < ledCount; i++) {
    const hex = document.getElementById(`pop-led-${i}`)?.value || '#000000';
    const {r, g, b} = hexToRgb(hex);
    leds.push({ index: i, r, g, b });
  }
  const effect = document.getElementById('pop-effect')?.value || 'solid';
  await apiPost('/direct', { action: 'set', leds, effect, params: {} });
  closeLedPopover();
}

// ── Alerts (tracks dismissed IDs — dismissed alerts stay gone until condition clears) ──

function dismissAlert(btn) {
  const banner = btn.parentElement;
  if (banner.dataset.alertId) _dismissedAlerts.add(banner.dataset.alertId);
  banner.remove();
}

function renderOverlayBadge(el, overlayName, overlayReason) {
  if (!el) return;
  if (!overlayName) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.dataset.overlay = overlayName;
  el.innerHTML = `<span class="overlay-name">${overlayName}</span> <span class="overlay-reason">${overlayReason || ''}</span>`;
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
}

// ── Mode popover ──

const MODE_DESCRIPTIONS = {
  status: 'Prometheus health → green breathing, amber warning, red critical',
  direct: 'Manual per-node LED control with color pickers',
  music: 'Synchronized beat sheet playback via NTP clock sync',
  'knight-rider': 'Red scanner sweep with trailing glow across all nodes',
  'rainbow-wave': 'Traveling hue rotation across all LEDs',
  'breathing': 'CPU-proportional pulse — fast when busy, slow when idle',
  'temperature': 'CPU thermal heatmap — blue cool, green warm, red hot',
  'network': 'Network throughput — blue RX, green TX, brightness = traffic',
  'morse': 'Morse code blinker — all LEDs flash white in unison',
  'countdown': 'Visual countdown timer — green→red→flash',
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
        const ledsPlaceholder = document.createElement('div');
        ledsPlaceholder.className = 'val';
        ledsPlaceholder.style.textAlign = 'center';
        ledsPlaceholder.style.fontSize = '9px';
        ledsPlaceholder.style.color = 'var(--muted)';
        ledsPlaceholder.textContent = '—';
        row.appendChild(ledsPlaceholder);
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
        ['status', 'device', 'last seen', 'clock'].forEach(label => {
          const item = document.createElement('div');
          item.className = 'detail-item';
          item.appendChild(document.createTextNode(label));
          const val = document.createElement('span');
          val.textContent = '--';
          item.appendChild(val);
          detail.appendChild(item);
        });
        row.appendChild(detail);
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

    if (node.present) {
      const ledsMini = row.querySelector('[data-role="leds-mini"]');
      if (ledsMini) {
        const minis = ledsMini.querySelectorAll('.led-mini');
        (node.leds || []).forEach((led, i) => {
          if (i >= minis.length) return;
          minis[i].style.background = `rgb(${led.r},${led.g},${led.b})`;
          minis[i].classList.toggle('led-mini-off', led.r === 0 && led.g === 0 && led.b === 0);
        });
      }
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
      if (expanded) {
        const spans = detail.querySelectorAll('.detail-item span');
        const d = node.device || {};
        if (node.present) {
          if (spans[0]) spans[0].textContent = d.serial || '--';
          if (spans[1]) spans[1].textContent = d.leds != null ? String(d.leds) : '--';
        } else {
          if (spans[0]) spans[0].textContent = node.online ? 'online' : 'offline';
          if (spans[1]) spans[1].textContent = 'no blinkstick';
        }
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
  const fetchAndUpdate = async () => {
    const data = await apiGet('/status');
    if (!data) return false;
    _lastStatusData = data;
    updateAllViews(data);
    return data.nodes?.some(n => n.device?.clock_skew_ms != null);
  };
  setTimeout(async () => { if (!await fetchAndUpdate()) setTimeout(fetchAndUpdate, 3000); }, 2000);
}

async function stopPlayback() { await apiPost('/songs/stop'); }

function updatePresetButtons(playback) {
  const song = playback?.playing ? (playback.song || '').replace(/^preset-/, '') : null;
  document.querySelectorAll('[data-preset]').forEach(btn => {
    btn.classList.toggle('primary', btn.dataset.preset === song);
  });
}

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

  apiPost('/clock/check').catch(() => {});

  const actions = document.getElementById('actions');
  if (actions) {
    actions.innerHTML = `
      <button class="btn" onclick="setMode('status')">Status</button>
      <div class="action-sep"></div>
      <button class="btn" data-preset="chase" onclick="playPreset('chase')">Chase</button>
      <button class="btn" data-preset="alternate" onclick="playPreset('alternate')">Alternate</button>
      <button class="btn" data-preset="rainbow" onclick="playPreset('rainbow')">Rainbow</button>
      <button class="btn" data-preset="flash" onclick="playPreset('flash')">Flash</button>
      <button class="btn" data-preset="police" onclick="playPreset('police')">Police</button>
      <div class="action-sep"></div>
      <button class="btn" data-action="all-off" onclick="allOff(this)">All off</button>
      <button class="btn danger" onclick="stopPlayback()">Stop</button>`;
  }
}

function updateAllOffButton(nodes) {
  const btn = document.querySelector('[data-action="all-off"]');
  if (!btn) return;
  const allOff = nodes?.filter(n => n.present).every(n =>
    !n.leds?.length || n.leds.every(l => l.r === 0 && l.g === 0 && l.b === 0)
  );
  btn.classList.toggle('danger', !!allOff);
}

async function allOff(btn) {
  await apiPost('/off');
  closeLedPopover();
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

  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeLedPopover(); });
  document.addEventListener('click', (e) => {
    if (_activePopoverNode && !e.target.closest('.node-unit')) closeLedPopover();
  });

  initGlobal();
  if (document.getElementById('panels')) initDashboard();
});
