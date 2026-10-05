// BlinkStick Beat Sheet Editor — step sequencer grid + YAML sync

const Editor = {
  data: null,
  nodes: [],
  brush: null,
  mode: 'visual',
  _undoStack: [],

  async init() {
    this.nodes = await apiGet('/nodes').then(ns => (ns || []).filter(n => n.present));
    if (!this.nodes.length) {
      document.getElementById('editor-grid').innerHTML = '<div class="empty-state">No nodes online</div>';
      return;
    }
    this.data = this._defaultSheet();
    this.brush = Object.keys(this.data.palette)[0] || null;
    document.addEventListener('mouseup', () => { Editor._painting = false; });
    document.addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'z') { e.preventDefault(); Editor.undo(); } });
    this.renderGrid();
    this.renderPalette();
  },

  _defaultSheet() {
    return {
      metadata: { name: 'new-song', title: '', author: '' },
      timing: { bpm: 120, loop: false, default_transition: 'solid' },
      on_end: 'status',
      palette: { 'R': '#FF0000', 'G': '#00FF00', 'B': '#0000FF', 'W': '#FFFFFF', '_': '#000000' },
      node_order: this.nodes.map(n => n.name),
      sections: {},
      beats: [],
    };
  },

  // ── Grid rendering ──

  renderGrid() {
    const container = document.getElementById('editor-grid');
    if (!container) return;
    const oldWrap = container.querySelector('.grid-wrap');
    const scrollPos = oldWrap ? oldWrap.scrollTop : 0;
    const nodeOrder = this.data.node_order;
    const n = nodeOrder.length;

    let html = '<div class="grid-wrap"><div class="grid">';
    // Header
    html += '<div class="grid-header"><div class="grid-header-cell"></div>';
    nodeOrder.forEach(name => {
      const short = name.replace('octolet-', '');
      const leds = this.nodes.find(nd => nd.name === name)?.leds || 2;
      const sub = Array.from({length: leds}, (_, i) => `L${i}`).join(' ');
      html += `<div class="grid-header-cell">${short}<span class="sub">${sub}</span></div>`;
    });
    html += '<div class="grid-header-cell"></div></div>';

    // Beats
    const expanded = this._expandBeats();
    expanded.forEach((beat, idx) => {
      if (beat.type === 'section_start') {
        html += `<div class="section-row"><div class="section-marker" style="display:table-cell">── ${beat.name} ──</div></div>`;
        return;
      }
      if (beat.type === 'section_end') {
        html += '<div class="section-row"><div class="section-marker end" style="display:table-cell">── end ──</div></div>';
        return;
      }

      const num = beat.num || idx + 1;
      const repeatBadge = beat.repeat ? ` <span class="hold-badge">×${beat.repeat}</span>` : '';
      const holdBadge = beat.hold ? ` <span class="hold-badge">hold ${beat.hold}</span>` : '';

      html += '<div class="grid-row">';
      html += `<div class="grid-num" onclick="Editor.fillRow(${idx})" title="Click to fill row">${num}${repeatBadge}${holdBadge}</div>`;

      nodeOrder.forEach((name, ni) => {
        const leds = this.nodes.find(nd => nd.name === name)?.leds || 2;
        html += '<div class="grid-cell">';
        for (let li = 0; li < leds; li++) {
          const color = beat.cells?.[ni]?.[li] || '#000000';
          const glow = color !== '#000000' ? `box-shadow:0 0 8px ${color}60` : '';
          html += `<span class="grid-led" style="background:${color};${glow}" data-beat="${idx}" data-node="${ni}" data-led="${li}" onmousedown="Editor.startPaint(${idx},${ni},${li})" onmouseover="Editor.dragPaint(${idx},${ni},${li})"></span>`;
        }
        html += '</div>';
      });

      html += `<div class="grid-actions"><button title="Move up" onclick="Editor.moveBeat(${idx},-1)">↑</button><button title="Move down" onclick="Editor.moveBeat(${idx},1)">↓</button><button title="Duplicate" onclick="Editor.dupBeat(${idx})">⧉</button><button title="Clear row" onclick="Editor.clearRow(${idx})">○</button><button title="Delete" onclick="Editor.delBeat(${idx})">×</button></div>`;
      html += '</div>';
    });

    // Add row
    html += '<div class="grid-row" style="opacity:0.4"><div class="grid-num" style="cursor:pointer" onclick="Editor.addBeat()">+</div>';
    nodeOrder.forEach(name => {
      const leds = this.nodes.find(nd => nd.name === name)?.leds || 2;
      html += '<div class="grid-cell">';
      for (let li = 0; li < leds; li++) html += '<span class="grid-led empty"></span>';
      html += '</div>';
    });
    html += '<div class="grid-actions"></div></div>';

    html += '</div></div>';
    container.innerHTML = html;
    requestAnimationFrame(() => {
      const newWrap = container.querySelector('.grid-wrap');
      if (newWrap) newWrap.scrollTop = scrollPos;
    });
  },

  renderPalette() {
    const container = document.getElementById('editor-palette');
    if (!container) return;
    const p = this.data.palette;
    let html = '';
    Object.entries(p).forEach(([key, color]) => {
      const sel = this.brush === key ? ' selected' : '';
      const border = color === '#000000' ? 'border-color:var(--border)' : '';
      html += `<div class="palette-swatch${sel}" style="background:${color};${border}" onclick="Editor.selectBrush('${key}')" title="${key}"><span class="palette-label">${key}</span></div>`;
    });
    html += '<button class="palette-add" onclick="Editor.addColor()" title="Add color">+</button>';
    const brushLabel = this.brush ? `brush: ${this.brush} (${p[this.brush]})` : 'click a swatch';
    html += `<span class="brush-label">${brushLabel}</span>`;
    container.innerHTML = html;
  },

  // ── Cell interaction ──

  clickCell(beatIdx, nodeIdx, ledIdx) {
    if (!this.brush) { showToast('Select a color from the palette first'); return; }
    const expanded = this._expandBeats();
    const beat = expanded[beatIdx];
    if (!beat || beat.type) return;
    if (!beat.cells) beat.cells = [];
    while (beat.cells.length <= nodeIdx) beat.cells.push([]);
    const leds = this.nodes[nodeIdx]?.leds || 2;
    while (beat.cells[nodeIdx].length < leds) beat.cells[nodeIdx].push('#000000');
    const color = this.data.palette[this.brush] || '#000000';
    beat.cells[nodeIdx][ledIdx] = color;
    const cell = document.querySelector(`[data-beat="${beatIdx}"][data-node="${nodeIdx}"][data-led="${ledIdx}"]`);
    if (cell) {
      cell.style.background = color;
      cell.style.boxShadow = color !== '#000000' ? `0 0 8px ${color}60` : '';
    }
  },

  _painting: false,

  startPaint(beatIdx, nodeIdx, ledIdx) {
    this._painting = true;
    this._pushUndo();
    this.clickCell(beatIdx, nodeIdx, ledIdx);
  },

  dragPaint(beatIdx, nodeIdx, ledIdx) {
    if (!this._painting) return;
    this.clickCell(beatIdx, nodeIdx, ledIdx);
  },

  fillRow(beatIdx) {
    if (!this.brush) { showToast('Select a color from the palette first'); return; }
    this._pushUndo();
    const expanded = this._expandBeats();
    const beat = expanded[beatIdx];
    if (!beat || beat.type) return;
    if (!beat.cells) beat.cells = [];
    const color = this.data.palette[this.brush] || '#000000';
    const n = this.data.node_order.length;
    for (let ni = 0; ni < n; ni++) {
      if (!beat.cells[ni]) beat.cells[ni] = [];
      const leds = this.nodes[ni]?.leds || 2;
      for (let li = 0; li < leds; li++) beat.cells[ni][li] = color;
    }
    const glow = color !== '#000000' ? `0 0 8px ${color}60` : '';
    document.querySelectorAll(`[data-beat="${beatIdx}"]`).forEach(cell => {
      cell.style.background = color;
      cell.style.boxShadow = glow;
    });
  },

  selectBrush(key) {
    this.brush = key;
    this.renderPalette();
  },

  addColor() {
    const container = document.getElementById('editor-palette');
    if (!container || container.querySelector('.palette-form')) return;
    const form = document.createElement('span');
    form.className = 'palette-form';
    form.style.cssText = 'display:inline-flex;gap:4px;align-items:center;margin-left:8px;vertical-align:top';
    form.innerHTML = `
      <input type="text" maxlength="1" placeholder="Key" style="width:44px;min-height:28px;padding:4px 8px;font-size:11px">
      <input type="color" value="#FF8800" style="min-height:28px;min-width:28px;padding:2px">
      <button class="btn" style="height:28px;font-size:10px" onclick="Editor.confirmAddColor()">Add</button>
      <button class="btn" style="height:28px;font-size:10px" onclick="this.parentElement.remove()">×</button>`;
    container.appendChild(form);
    const textInput = form.querySelector('input[type="text"]');
    textInput.focus();
    textInput.addEventListener('keydown', e => { if (e.key === 'Enter') Editor.confirmAddColor(); });
  },

  confirmAddColor() {
    const form = document.querySelector('.palette-form');
    if (!form) return;
    const key = form.querySelector('input[type="text"]').value.trim();
    const color = form.querySelector('input[type="color"]').value;
    if (!key || key.length !== 1) { showToast('Key must be a single character', 'error'); return; }
    this.data.palette[key] = color;
    form.remove();
    this.renderPalette();
  },

  // ── Beat CRUD ──

  addBeat() {
    this._pushUndo();
    const n = this.data.node_order.length;
    const leds = this.nodes[0]?.leds || 2;
    const cells = Array.from({length: n}, () => Array.from({length: leds}, () => '#000000'));
    this._expandBeats().push({ cells });
    this.renderGrid();
    requestAnimationFrame(() => {
      const rows = document.querySelectorAll('.grid-row');
      if (rows.length > 1) rows[rows.length - 2]?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  },

  dupBeat(idx) {
    this._pushUndo();
    const expanded = this._expandBeats();
    const beat = expanded[idx];
    if (!beat || beat.type) return;
    const copy = JSON.parse(JSON.stringify(beat));
    expanded.splice(idx + 1, 0, copy);
    this.renderGrid();
  },

  delBeat(idx) {
    this._pushUndo();
    const expanded = this._expandBeats();
    if (expanded[idx]?.type) return;
    expanded.splice(idx, 1);
    this.renderGrid();
  },

  moveBeat(idx, dir) {
    this._pushUndo();
    const expanded = this._expandBeats();
    const target = idx + dir;
    if (target < 0 || target >= expanded.length) return;
    if (expanded[idx]?.type || expanded[target]?.type) return;
    [expanded[idx], expanded[target]] = [expanded[target], expanded[idx]];
    this.renderGrid();
  },

  clearRow(beatIdx) {
    this._pushUndo();
    const expanded = this._expandBeats();
    const beat = expanded[beatIdx];
    if (!beat || beat.type) return;
    if (!beat.cells) return;
    beat.cells.forEach(node => { node.forEach((_, li) => { node[li] = '#000000'; }); });
    document.querySelectorAll(`[data-beat="${beatIdx}"]`).forEach(cell => {
      cell.style.background = '#000000';
      cell.style.boxShadow = '';
    });
  },

  addSection() {
    const container = document.getElementById('editor-grid');
    if (!container || container.querySelector('.section-form')) return;
    const target = container.querySelector('.grid-wrap') || container;
    const form = document.createElement('div');
    form.className = 'section-form';
    form.style.cssText = 'display:flex;gap:6px;align-items:center;padding:8px 0';
    form.innerHTML = `
      <input type="text" placeholder="Section name" style="width:160px;min-height:28px;padding:4px 8px;font-size:11px">
      <button class="btn" style="height:28px;font-size:10px" onclick="Editor.confirmAddSection()">Add</button>
      <button class="btn" style="height:28px;font-size:10px" onclick="this.parentElement.remove()">×</button>`;
    target.appendChild(form);
    form.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    const textInput = form.querySelector('input');
    textInput.focus();
    textInput.addEventListener('keydown', e => { if (e.key === 'Enter') Editor.confirmAddSection(); });
  },

  confirmAddSection() {
    const form = document.querySelector('.section-form');
    if (!form) return;
    const name = form.querySelector('input').value.trim();
    if (!name) { showToast('Section name required', 'error'); return; }
    const expanded = this._expandBeats();
    expanded.push({ type: 'section_start', name });
    expanded.push({ type: 'section_end' });
    form.remove();
    this.renderGrid();
  },

  // ── Expand/flatten ──

  _expandedCache: null,

  _expandBeats() {
    if (!this._expandedCache) {
      this._expandedCache = [];
      const beats = this.data.beats || [];
      let num = 1;
      beats.forEach(b => {
        if (typeof b === 'string') {
          this._expandedCache.push({ cells: this._stringToCells(b), num: num++ });
        } else if (b.section) {
          this._expandedCache.push({ type: 'section_start', name: b.section });
          (this.data.sections[b.section] || []).forEach(s => {
            this._expandedCache.push({ cells: this._stringToCells(s), num: num++ });
          });
          this._expandedCache.push({ type: 'section_end' });
        } else if (b.repeat) {
          this._expandedCache.push({ cells: this._stringToCells(b.repeat), num: num++, repeat: b.count || 1 });
        } else if (b.colors) {
          this._expandedCache.push({ cells: this._stringToCells(b.colors), num: num++, hold: b.hold || 1 });
        }
      });
    }
    return this._expandedCache;
  },

  _stringToCells(str) {
    const n = this.data.node_order.length;
    const chars = str.split('');
    const perNode = Math.max(1, Math.floor(chars.length / n));
    const cells = [];
    for (let i = 0; i < n; i++) {
      const leds = this.nodes[i]?.leds || 2;
      const nodeCells = [];
      for (let j = 0; j < leds; j++) {
        const ci = i * perNode + (perNode > 1 ? j : 0);
        const ch = chars[ci] || chars[i] || '_';
        nodeCells.push(this.data.palette[ch] || '#000000');
      }
      cells.push(nodeCells);
    }
    return cells;
  },

  _cellsToString(cells) {
    if (!cells) return '';
    const palette = this.data.palette || {};
    const rev = {};
    Object.entries(palette).forEach(([k, v]) => { rev[v.toUpperCase()] = k; });
    let s = '';
    cells.forEach(nodeLeds => {
      (nodeLeds || []).forEach(color => {
        s += rev[(color || '#000000').toUpperCase()] || '_';
      });
    });
    return s;
  },

  _compactBeats() {
    const expanded = this._expandBeats();
    const beats = [];
    const sections = {};
    let sectionName = null;
    let sectionBeats = [];

    expanded.forEach(entry => {
      if (entry.type === 'section_start') {
        sectionName = entry.name;
        sectionBeats = [];
        return;
      }
      if (entry.type === 'section_end') {
        if (sectionName) {
          sections[sectionName] = sectionBeats;
          beats.push({ section: sectionName });
        }
        sectionName = null;
        return;
      }

      const str = this._cellsToString(entry.cells);
      const item = entry.repeat ? { repeat: str, count: entry.repeat }
                 : entry.hold ? { colors: str, hold: entry.hold }
                 : str;

      if (sectionName) sectionBeats.push(str);
      else beats.push(item);
    });

    return { beats, sections };
  },

  _invalidateCache() {
    this._expandedCache = null;
  },

  _pushUndo() {
    const snapshot = JSON.stringify(this._expandBeats());
    this._undoStack.push(snapshot);
    if (this._undoStack.length > 20) this._undoStack.shift();
  },

  undo() {
    if (!this._undoStack.length) { showToast('Nothing to undo'); return; }
    this._expandedCache = JSON.parse(this._undoStack.pop());
    this.renderGrid();
    this.renderPalette();
  },

  // ── YAML sync ──

  toYaml() {
    const d = this.data;
    if (!d) return '';
    try {
      const { beats, sections } = this._compactBeats();
      d.beats = beats;
      d.sections = sections;
    } catch (e) {
      console.warn('_compactBeats error, using raw data:', e.message);
    }
    const sheet = {
      apiVersion: 'blinkstick.octolet.int/v1',
      kind: 'BeatSheet',
      metadata: d.metadata || {},
      timing: d.timing || {},
      on_end: d.on_end || 'status',
      palette: d.palette || {},
      node_order: d.node_order || [],
      sections: d.sections || {},
      beats: d.beats || [],
    };
    return jsyaml ? jsyaml.dump(sheet) : JSON.stringify(sheet, null, 2);
  },

  fromYaml(yamlStr) {
    try {
      const parsed = jsyaml ? jsyaml.load(yamlStr) : JSON.parse(yamlStr);
      this.data = parsed;
      this._invalidateCache();
      this.renderGrid();
      this.renderPalette();
      this.syncMetadata();
      return null;
    } catch (e) {
      return e.message;
    }
  },

  syncMetadata() {
    const d = this.data;
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
    set('meta-name', d.metadata?.name || '');
    set('meta-title', d.metadata?.title || '');
    set('meta-author', d.metadata?.author || '');
    set('timing-bpm', d.timing?.bpm || 120);
    set('timing-loop', d.timing?.loop ? 'yes' : 'no');
    set('timing-transition', d.timing?.default_transition || 'solid');
    set('timing-onend', d.on_end || 'status');
  },

  readMetadata() {
    const get = (id) => document.getElementById(id)?.value || '';
    this.data.metadata = { name: get('meta-name'), title: get('meta-title'), author: get('meta-author') };
    this.data.timing = {
      bpm: parseInt(get('timing-bpm')) || 120,
      loop: get('timing-loop') === 'yes',
      default_transition: get('timing-transition'),
    };
    this.data.on_end = get('timing-onend') || 'status';
  },

  // ── Actions ──

  async save() {
    this.readMetadata();
    const yaml = this.toYaml();
    const result = await apiPost('/songs', { yaml_content: yaml });
    if (result.error) showToast(result.error, 'error');
    else showToast(`Saved: ${this.data.metadata.name}`);
  },

  exportYaml() {
    this.readMetadata();
    const yaml = this.toYaml();
    const blob = new Blob([yaml], { type: 'text/yaml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${this.data.metadata.name || 'beat-sheet'}.yaml`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  },

  async preview() {
    this.readMetadata();
    const name = this.data.metadata.name;
    await this.save();
    await apiPost(`/songs/${name}/play`);
  },

  async stop() {
    await apiPost('/songs/stop');
  },

  // ── Load existing song ──

  async loadSong(name) {
    const detail = await apiGet(`/songs/${name}`);
    if (detail?.beat_sheet) {
      this.data = detail.beat_sheet;
      this.brush = Object.keys(this.data.palette || {})[0] || null;
      this._invalidateCache();
      this.renderGrid();
      this.renderPalette();
      this.syncMetadata();
      showVisual();
    }
  },
};

// js-yaml loaded from CDN in music.html
const jsyaml = window.jsyaml || null;

function showVisual() {
  document.getElementById('visual-pane').classList.add('active');
  document.getElementById('yaml-pane').classList.remove('active');
  document.querySelectorAll('.ed-tab').forEach((t, i) => {
    t.classList.toggle('active', i === 0);
    t.setAttribute('aria-selected', i === 0 ? 'true' : 'false');
  });
}

function showCode() {
  try {
    Editor.readMetadata();
    const yamlContent = Editor.toYaml();
    document.getElementById('yaml-editor').value = yamlContent;
  } catch (e) {
    console.error('showCode error:', e);
    document.getElementById('yaml-editor').value = `# Error generating YAML: ${e.message}\n# Raw data:\n${JSON.stringify(Editor.data, null, 2)}`;
  }
  document.getElementById('yaml-pane').classList.add('active');
  document.getElementById('visual-pane').classList.remove('active');
  document.querySelectorAll('.ed-tab').forEach((t, i) => {
    t.classList.toggle('active', i === 1);
    t.setAttribute('aria-selected', i === 1 ? 'true' : 'false');
  });
}

function applyYaml() {
  const yaml = document.getElementById('yaml-editor').value;
  const err = Editor.fromYaml(yaml);
  const errEl = document.getElementById('parse-error');
  if (err) {
    errEl.textContent = err;
    errEl.hidden = false;
  } else {
    errEl.hidden = true;
    showVisual();
  }
}
