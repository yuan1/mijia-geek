const $ = id => document.getElementById(id);
let devices = [];
let rules = [];
let statusData = {};
let previewDetail = null;
let editorLoaded = false;
let tokenRejected = false;

const TOKEN_KEY = 'mijia_geek_api_token';
const getToken = () => localStorage.getItem(TOKEN_KEY) || '';
const setToken = t => localStorage.setItem(TOKEN_KEY, t || '');

function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  }[c]));
}

async function api(path, opts = {}, retried = false) {
  const headers = Object.assign({'Content-Type':'application/json'}, opts.headers || {});
  const token = getToken();
  if (token) headers['X-API-Token'] = token;

  const r = await fetch(path, {...opts, headers});
  if (r.status === 401 && !retried && !tokenRejected) {
    const t = prompt('请输入 API Token（若未启用可留空）');
    if (t !== null) {
      setToken(t.trim());
      tokenRejected = false;
      return api(path, opts, true);
    }
    tokenRejected = true;
  }
  if (r.ok) tokenRejected = false;
  const txt = await r.text();
  let data;
  try { data = JSON.parse(txt); } catch (e) { data = {detail: txt}; }
  if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
  return data;
}

function showMsg(el, text, ok) {
  el.textContent = text;
  el.className = 'msg ' + (ok ? 'ok' : 'err');
}

async function refreshStatus() {
  try {
    const s = await api('api/status');
    statusData = s;
    const b = $('connBadge');
    if (s.connected) {
      b.className = 'badge on'; b.innerHTML = '<span class="dot"></span>已连接';
    } else {
      b.className = 'badge off'; b.innerHTML = '<span class="dot"></span>未连接';
    }
    $('gwAddr').textContent = s.gateway_url + (s.has_passcode ? ' · 会话已登录' : '');
    $('sDev').textContent = s.device_count ?? '-';
    $('sOnline').textContent = s.online_count ?? '-';
    $('sRule').textContent = s.rule_count ?? '-';
    $('sEnabled').textContent = s.enabled_count ?? '-';
    if (s.last_error) showMsg($('errMsg'), '最近错误：' + s.last_error, false);
    else $('errMsg').className = 'msg';
    $('editorUrl').value = s.gateway_web_url || '';
    if (s.connected) loadDevices();
  } catch (e) {
    const b = $('connBadge');
    b.className = 'badge off';
    const auth = String(e.message || '').toLowerCase().includes('unauthorized');
    b.innerHTML = `<span class="dot"></span>${auth ? '需要 Token' : '服务异常'}`;
  }
}

async function loadDevices() {
  try {
    const d = await api('api/devices');
    devices = Object.entries(d.devices || {}).map(([id, v]) => ({id, ...v}));
    renderDevices();
  } catch (e) {}
}

function renderDevices() {
  const q = ($('devFilter').value || '').toLowerCase();
  const list = devices.filter(d => {
    if (!q) return true;
    const s = `${d.name||''} ${d.roomName||''} ${d.modelName||''} ${d.model||''}`.toLowerCase();
    return s.includes(q);
  });
  const box = $('devList');
  if (!list.length) { box.innerHTML = '<div class="hint">无匹配设备</div>'; return; }
  box.innerHTML = list.map(d => `
    <div class="item">
      <span class="nm">${esc(d.name || d.modelName || d.model || d.id)}</span>
      <span class="meta">${esc(d.roomName || '')} · ${d.online ? '在线' : '离线'}</span>
    </div>`).join('');
}

async function loadRules() {
  try {
    const d = await api('api/rules');
    rules = d.rules || [];
    renderRules();
  } catch (e) {
    const box = $('ruleList');
    box.textContent = '读取失败：' + e.message;
  }
}

function ruleName(r) {
  return r.name || (r.userData && r.userData.name) || r.id;
}
function ruleEnabled(r) {
  const value = r.enabled ?? r.enable ?? (r.cfg && r.cfg.enable);
  return value === true || value === 'true' || value === 1 || value === '1';
}
function ruleUpdateTime(r) {
  return r.updateTime ?? (r.userData && r.userData.lastUpdateTime);
}

function renderRules() {
  const box = $('ruleList');
  if (!rules.length) {
    box.innerHTML = '<div class="hint">极客版里还没有规则。先在米家 App 或官方编辑器创建一条试试。</div>';
    return;
  }
  box.innerHTML = rules.map(r => {
    const on = ruleEnabled(r);
    return `
    <div class="item">
      <span class="nm">${esc(ruleName(r))}</span>
      <span class="meta">${on ? '已启用' : '已停用'}</span>
      <button class="ghost small" data-preview="${esc(r.id)}">预览</button>
      <div class="sw ${on ? 'on' : ''}" data-id="${esc(r.id)}" data-on="${on ? 'true' : 'false'}"></div>
    </div>`;
  }).join('');

  box.querySelectorAll('[data-preview]').forEach(btn => {
    btn.onclick = () => openPreview(btn.dataset.preview);
  });
  box.querySelectorAll('.sw').forEach(el => {
    el.onclick = async () => {
      const id = el.dataset.id;
      const now = el.dataset.on === 'true';
      el.style.opacity = .5;
      try {
        await api(`api/rule/${encodeURIComponent(id)}/toggle`, {
          method:'POST', body: JSON.stringify({enable: !now})
        });
        await loadRules(); await refreshStatus();
      } catch (e) { alert('切换失败: ' + e.message); }
      finally { el.style.opacity = 1; }
    };
  });
}

function deviceName(did) {
  const d = devices.find(x => String(x.id) === String(did));
  return d ? (d.name || d.modelName || d.model || did) : (did || '未知设备');
}

function shortJson(v, limit = 80) {
  let text;
  try { text = JSON.stringify(v); } catch (e) { text = String(v); }
  if (!text) return '';
  return text.length > limit ? text.slice(0, limit - 1) + '…' : text;
}

function nodeSummary(node) {
  const type = node.type || 'unknown';
  const p = node.props || {};
  const cfg = node.cfg || {};
  const name = deviceName(p.did);
  switch (type) {
    case 'deviceInput': {
      const point = p.eiid !== undefined ? `事件 ${p.siid}.${p.eiid}`
        : `属性 ${p.siid}.${p.piid}`;
      return `${name} ${point} ${p.operator || ''} ${shortJson(p.value)}`.trim();
    }
    case 'deviceOutput': {
      const point = p.aiid !== undefined ? `动作 ${p.siid}.${p.aiid}`
        : `属性 ${p.siid}.${p.piid}`;
      return `${name} ${point} ${shortJson(p.params ?? p.value)}`.trim();
    }
    case 'deviceGet':
      return `${name} 查询 ${p.siid}.${p.piid}`;
    case 'deviceGetSetVar':
      return `${name} 查询→变量 ${p.varId || p.variable || ''}`;
    case 'condition':
      return `当 ${shortJson(p.value ?? cfg.value)} 满足条件`;
    case 'delay':
      return `延迟 ${p.timeout ?? cfg.timeout ?? '?'} ms`;
    case 'timer':
    case 'alarmClock':
      return `定时 ${p.hour ?? cfg.hour ?? '?'}:${String(p.minute ?? cfg.minute ?? '').padStart(2,'0')}`;
    case 'logicAnd': return '满足全部条件';
    case 'logicOr': return '满足任一条件';
    case 'logicNot': return '状态取反';
    case 'signalOr': return '事件或';
    case 'timeRange': return `时间段 ${shortJson(cfg.value ?? p.value)}`;
    case 'varGet': return `读取变量 ${p.varId || p.id || ''}`;
    case 'varSetNumber':
    case 'varSetString':
      return `设置变量 ${p.varId || p.id || ''} = ${shortJson(p.value)}`;
    case 'loop': return '循环';
    case 'counter':
    case 'onlyNTimes': return '次数条件';
    case 'modeSwitch': return '模式切换';
    case 'onLoad': return '本自动化启用时';
    case 'register': return '自定义状态';
    case 'statusLast': return '状态持续';
    case 'eventSequence': return '事件先后发生';
    case 'nop': return cfg.name || '注释节点';
    default:
      return shortJson(p) || shortJson(cfg) || node.id || '';
  }
}

const NODE_COLORS = {
  deviceInput:'#2e7d32', deviceInputSetVar:'#2e7d32', onLoad:'#2e7d32',
  alarmClock:'#2e7d32', timeRange:'#6a1b9a', varChange:'#2e7d32',
  deviceOutput:'#1565c0', deviceGetSetVar:'#1565c0',
  varSetNumber:'#1565c0', varSetString:'#1565c0',
  deviceGet:'#ef6c00', varGet:'#ef6c00', condition:'#ef6c00',
  logicAnd:'#ef6c00', logicOr:'#ef6c00', logicNot:'#ef6c00',
  signalOr:'#ef6c00', statusLast:'#ef6c00', counter:'#ef6c00',
  onlyNTimes:'#ef6c00', eventSequence:'#ef6c00', modeSwitch:'#ef6c00',
  delay:'#5e35b1', loop:'#5e35b1', timer:'#5e35b1', nop:'#455a64'
};

function parseTarget(v) {
  if (Array.isArray(v) && v.length >= 2) {
    return {id: String(v[0]), port: String(v[1])};
  }
  if (typeof v === 'string') {
    const i = v.indexOf('.');
    if (i > 0 && i < v.length - 1) {
      return {id: v.slice(0, i), port: v.slice(i + 1)};
    }
  }
  return null;
}

function edgeTargets(node) {
  const out = [];
  const outputs = node.outputs || {};
  for (const [port, targets] of Object.entries(outputs)) {
    if (!Array.isArray(targets)) continue;
    for (const target of targets) {
      const parsed = parseTarget(target);
      if (parsed) out.push({...parsed, port, raw: target});
    }
  }
  return out;
}

function layoutNodes(nodes) {
  const positions = new Map();
  let hasPos = false;
  for (const node of nodes) {
    const pos = node.cfg && node.cfg.pos;
    if (pos && Number.isFinite(Number(pos.x)) && Number.isFinite(Number(pos.y))) {
      hasPos = true;
      positions.set(node.id, {
        x: Number(pos.x), y: Number(pos.y),
        w: Number(pos.width) || 230, h: Number(pos.height) || 100
      });
    }
  }

  if (!hasPos) {
    const indeg = new Map(nodes.map(n => [n.id, 0]));
    for (const node of nodes) {
      for (const t of edgeTargets(node)) {
        if (indeg.has(t.id)) indeg.set(t.id, indeg.get(t.id) + 1);
      }
    }
    const levels = new Map();
    const queue = nodes.filter(n => (indeg.get(n.id) || 0) === 0).map(n => n.id);
    if (!queue.length) nodes.forEach(n => queue.push(n.id));
    const seen = new Set();
    while (queue.length) {
      const id = queue.shift();
      if (seen.has(id)) continue;
      seen.add(id);
      const node = nodes.find(n => n.id === id);
      if (!node) continue;
      const level = levels.get(id) || 0;
      for (const t of edgeTargets(node)) {
        if (!seen.has(t.id)) {
          levels.set(t.id, Math.max(levels.get(t.id) || 0, level + 1));
          queue.push(t.id);
        }
      }
    }
    let index = 0;
    for (const node of nodes) {
      const level = levels.get(node.id) || 0;
      positions.set(node.id, {x: 40 + level * 310, y: 40 + index * 150, w: 230, h: 100});
      index += 1;
    }
  } else {
    let fallback = 0;
    for (const node of nodes) {
      if (!positions.has(node.id)) {
        positions.set(node.id, {x: 40 + (fallback % 3) * 310, y: 40 + Math.floor(fallback / 3) * 150, w: 230, h: 100});
        fallback += 1;
      }
    }
  }
  return positions;
}

function lintNodes(nodes) {
  const issues = [];
  const byId = new Map();
  const incoming = new Set();
  const sinks = new Set(['deviceOutput','deviceGetSetVar','varSetNumber','varSetString']);
  const events = new Set(['deviceInput','deviceInputSetVar','alarmClock','onLoad','varChange','signalOr']);

  for (const node of nodes) {
    if (byId.has(node.id)) {
      issues.push({level:'error', text:`节点 ID 重复：${node.id}`});
    }
    byId.set(node.id, node);
  }

  for (const node of nodes) {
    for (const t of edgeTargets(node)) {
      const target = byId.get(t.id);
      if (!target) {
        issues.push({level:'error', text:`${node.id}.${t.port} 指向不存在的节点 ${t.id}`});
        continue;
      }
      if (!Object.prototype.hasOwnProperty.call(target.inputs || {}, t.port)) {
        issues.push({level:'error', text:`${node.id}.${t.port} 指向 ${t.id} 未声明的输入端口 ${t.port}`});
      }
      incoming.add(`${t.id}.${t.port}`);
    }
  }

  for (const node of nodes) {
    if (node.type === 'condition') {
      if (!incoming.has(`${node.id}.trigger`))
        issues.push({level:'error', text:`condition ${node.id} 缺少 trigger 来源`});
      if (!incoming.has(`${node.id}.condition`))
        issues.push({level:'error', text:`condition ${node.id} 缺少 condition 来源`});
    }
    if (sinks.has(node.type) && !incoming.has(`${node.id}.trigger`) && !incoming.has(`${node.id}.input`)) {
      issues.push({level:'error', text:`执行节点 ${node.id} 没有上游来源`});
    }
  }

  const reachable = new Set();
  const queue = nodes.filter(n => events.has(n.type)).map(n => n.id);
  while (queue.length) {
    const id = queue.shift();
    if (!id || reachable.has(id)) continue;
    reachable.add(id);
    const node = byId.get(id);
    if (!node) continue;
    for (const t of edgeTargets(node)) {
      if (!reachable.has(t.id)) queue.push(t.id);
    }
  }
  for (const node of nodes) {
    if (sinks.has(node.type) && !reachable.has(node.id)) {
      issues.push({level:'warn', text:`执行节点 ${node.id} 不在任何事件源可达路径上`});
    }
  }

  if (!issues.length) issues.push({level:'ok', text:'基础结构校验通过（仅前端只读检查）'});
  return issues;
}

function renderGraph(nodes) {
  const positions = layoutNodes(nodes);
  let maxX = 0, maxY = 0;
  for (const pos of positions.values()) {
    maxX = Math.max(maxX, pos.x + pos.w);
    maxY = Math.max(maxY, pos.y + pos.h);
  }
  const width = Math.max(640, maxX + 60);
  const height = Math.max(320, maxY + 60);

  let svg = `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`;
  svg += '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#5b7085"/></marker></defs>';

  for (const node of nodes) {
    const p1 = positions.get(node.id);
    if (!p1) continue;
    for (const t of edgeTargets(node)) {
      const p2 = positions.get(t.id);
      if (!p2) continue;
      const x1 = p1.x + p1.w;
      const y1 = p1.y + p1.h / 2;
      const x2 = p2.x;
      const y2 = p2.y + p2.h / 2;
      const mx = (x1 + x2) / 2;
      svg += `<path d="M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}" fill="none" stroke="#5b7085" stroke-width="1.5" marker-end="url(#arrow)"/>`;
      svg += `<text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 6}" fill="#8fa4b8" font-size="10" text-anchor="middle">${esc(t.port)}</text>`;
    }
  }

  for (const node of nodes) {
    const pos = positions.get(node.id);
    if (!pos) continue;
    const color = NODE_COLORS[node.type] || '#455a64';
    const summary = nodeSummary(node);
    const typeLabel = node.type || 'unknown';
    const title = `${typeLabel} · ${summary}`;
    svg += `<g class="node">`;
    svg += `<title>${esc(title)}</title>`;
    svg += `<rect x="${pos.x}" y="${pos.y}" width="${pos.w}" height="${pos.h}" rx="10" fill="#151c24" stroke="${color}" stroke-width="1.6"/>`;
    svg += `<rect x="${pos.x}" y="${pos.y}" width="${pos.w}" height="24" rx="10" fill="${color}" opacity="0.9"/>`;
    svg += `<text x="${pos.x + 10}" y="${pos.y + 16}" fill="#ffffff" font-size="11" font-weight="700">${esc(typeLabel)}</text>`;
    svg += `<text x="${pos.x + 10}" y="${pos.y + 48}" fill="#dbe7f3" font-size="12">${esc(summary.slice(0, 34))}</text>`;
    if (summary.length > 34) {
      svg += `<text x="${pos.x + 10}" y="${pos.y + 66}" fill="#a7b6c5" font-size="11">${esc(summary.slice(34, 68))}</text>`;
    }
    svg += `<text x="${pos.x + 10}" y="${pos.y + pos.h - 10}" fill="#7a8a9a" font-size="10">${esc(node.id)}</text>`;
    svg += `</g>`;
  }
  svg += '</svg>';
  $('rpGraph').innerHTML = svg;
}

async function openPreview(ruleId) {
  const panel = $('rulePreview');
  panel.hidden = false;
  $('rpName').textContent = '加载中…';
  $('rpMeta').textContent = ruleId;
  $('rpIssues').innerHTML = '';
  $('rpGraph').innerHTML = '';
  $('rpJson').textContent = '';
  try {
    const detail = await api(`api/rule/${encodeURIComponent(ruleId)}`);
    previewDetail = detail;
    const nodes = Array.isArray(detail.nodes) ? detail.nodes : [];
    const rule = rules.find(r => String(r.id) === String(ruleId)) || {};
    $('rpName').textContent = ruleName(rule);
    const updated = ruleUpdateTime(rule);
    const parts = [`节点 ${nodes.length} 个`, ruleEnabled(rule) ? '已启用' : '已停用'];
    if (updated) parts.push(`更新 ${new Date(updated).toLocaleString()}`);
    $('rpMeta').textContent = parts.join(' · ');
    $('rpJson').textContent = JSON.stringify(detail, null, 2);

    const issues = lintNodes(nodes);
    $('rpIssues').innerHTML = issues.map(i =>
      `<div class="issue ${i.level}">${esc(i.text)}</div>`).join('');

    if (nodes.length) renderGraph(nodes);
    else $('rpGraph').innerHTML = '<div class="hint" style="padding:20px">该规则没有节点。</div>';

    panel.scrollIntoView({behavior:'smooth', block:'nearest'});
  } catch (e) {
    $('rpName').textContent = '预览失败';
    $('rpIssues').innerHTML = `<div class="issue error">${esc(e.message)}</div>`;
  }
}

function closePreview() {
  $('rulePreview').hidden = true;
  previewDetail = null;
}

function ensureEditor(force = false) {
  const url = statusData.gateway_web_url || '';
  const note = $('editorNote');
  $('editorUrl').value = url;
  if (!url) {
    $('editorFrame').hidden = true;
    note.textContent = '未配置官方编辑器地址。请设置 GATEWAY_WEB_URL，或让 Bridge 从 GATEWAY_URL 推导（默认端口 8086）。';
    return;
  }
  const mixed = location.protocol === 'https:' && url.startsWith('http://');
  if (mixed) {
    $('editorFrame').src = 'about:blank';
    $('editorFrame').hidden = true;
    editorLoaded = false;
    note.textContent = '⚠️ 当前页面是 HTTPS，官方编辑器是 HTTP，浏览器会阻止 iframe；请使用「新窗口打开」。';
    return;
  }
  $('editorFrame').hidden = false;
  note.textContent = '官方编辑器仍需要输入网关登录码；它不经过 Bridge，直接访问网关。';
  if (force || !editorLoaded) {
    $('editorFrame').src = url;
    editorLoaded = true;
  }
}

async function readLog(page) {
  try {
    const d = await api('api/logs?page=' + page);
    $('logBox').textContent = d.log || '(该页无日志)';
  } catch (e) {
    $('logBox').textContent = '读取失败: ' + e.message;
  }
}

const BRIDGE_BASE = new URL('.', window.location.href).toString().replace(/\/$/, '');
const AI_PROMPT = `你是米家极客版规则助手。请通过 Bridge REST 读取设备/规则/变量，生成规则节点图，并在写入前做结构校验。
Bridge 地址：${BRIDGE_BASE || 'http://<bridge>'}

如果客户端支持 MCP，可直接使用 bridge 自带的 MCP 工具（只读默认启用；写入需 MCP_ALLOW_WRITE=1）。

只读接口：
- GET /api/devices
- GET /api/rules
- GET /api/rule/{id}
- GET /api/variables?scope=global
- GET /api/variable/scopes
- GET /api/logs?page=0
- GET /api/events（SSE）

写入接口：
- PUT /api/rule/{id}  body: {"nodes":[...],"cfg":{...},"name":"..."}
- POST /api/rule/{id}/toggle  body: {"enable":true|false}
- DELETE /api/rule/{id}
- POST /api/variables / PUT /api/variable/{id} / DELETE /api/variable/{id}

规则结构：
- 节点：{id,type,cfg:{urn,pos:{x,y,width,height}},props,inputs,outputs}
- 连线：outputs = {"output":[["targetNode","trigger"]]}
- cfg：{id,enable,uiType:"graph",userData:{name,lastUpdateTime,transform:{x,y,scale,rotate}}}
- 常见 type：deviceInput, deviceOutput, deviceGet, condition, delay, timer, logicAnd/Or/Not, signalOr, timeRange, varGet, varSetNumber/String, loop, counter, statusLast, eventSequence, modeSwitch, onLoad

安全流程：
1. 先只读导出并确认设备 siid/piid/aiid；
2. 生成节点图并检查唯一 ID、端口、连线可达性；
3. 新规则默认 enable=false；
4. 保存到 PUT /api/rule/{id}；
5. 重新 GET /api/rule/{id} 读回验证；
6. 不要删除/覆盖未知规则；变量引用先检查。`;

$('btnCopyPrompt').onclick = async () => {
  try { await navigator.clipboard.writeText(AI_PROMPT); showMsg($('connMsg'), '提示词已复制', true); }
  catch (e) { alert('复制失败: ' + e.message); }
};

$('rpClose').onclick = closePreview;
$('rpCopy').onclick = async () => {
  const text = previewDetail ? JSON.stringify(previewDetail, null, 2) : '';
  if (!text) return;
  try { await navigator.clipboard.writeText(text); }
  catch (e) { alert('复制失败: ' + e.message); }
};

$('btnEditorLoad').onclick = () => ensureEditor(true);
$('btnEditorOpen').onclick = () => {
  const url = $('editorUrl').value;
  if (url) window.open(url, '_blank', 'noopener');
};

$('btnConnect').onclick = async () => {
  const code = $('passcode').value.trim();
  if (!/^\d{6}$/.test(code)) {
    showMsg($('connMsg'), '请输入 6 位数字', false);
    return;
  }
  const btn = $('btnConnect');
  btn.disabled = true; btn.textContent = '连接中…';
  try {
    const r = await api('api/connect', {
      method:'POST', body: JSON.stringify({passcode: code})
    });
    showMsg($('connMsg'), `连接成功！${r.device_count} 个设备，${r.rule_count} 条规则`, true);
    $('passcode').value = '';
    await refreshStatus(); await loadRules();
  } catch (e) {
    showMsg($('connMsg'), '连接失败：' + e.message, false);
  } finally {
    btn.disabled = false; btn.textContent = '连接';
  }
};

$('btnRefresh').onclick = async () => {
  try {
    await api('api/refresh', {method:'POST'});
    await loadDevices(); await refreshStatus();
  } catch (e) { alert('刷新失败: ' + e.message); }
};

$('devFilter').oninput = renderDevices;
$('btnLog0').onclick = () => readLog(0);
$('btnLog1').onclick = () => readLog(1);
$('btnLog2').onclick = () => readLog(2);

document.querySelectorAll('.tab').forEach(t => {
  t.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.pane').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    $('pane-' + t.dataset.pane).classList.add('active');
    if (t.dataset.pane === 'rules') loadRules();
    if (t.dataset.pane === 'editor') ensureEditor();
    if (t.dataset.pane === 'ai') $('aiPrompt').textContent = AI_PROMPT;
  };
});

$('aiPrompt').textContent = AI_PROMPT;
refreshStatus();
setInterval(refreshStatus, 15000);
