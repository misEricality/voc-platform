/* 灵听 Lynx 原型 —— 全页面脚本 v5（单页：左看板 + 右列表/AI）
   注：dashboard.js 由构建脚本拼接在本文件末尾（同一 <script> 作用域） */
const DATA = /*__DATA__*/ null;
const VOICES = DATA.voices, OPINIONS = DATA.opinions;
const GAMES = DATA.games, TAG_TREE = DATA.tags;
const PAGE_SIZE = 20;

/* 预计算 id 集合（用于 ID 筛选类型识别） */
const VOICE_ID_SET = new Set(VOICES.map(v => String(v.source_id)));
const OPINION_ID_SET = new Set(OPINIONS.map(o => String(o.id)));

/* ---------------- 工具 ---------------- */
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
function esc(s){return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
const SENTI_LABEL = {positive:'正向', neutral:'中性', negative:'负向'};
const SENTI_CLASS = {positive:'t-pos', neutral:'t-neu', negative:'t-neg'};
function fmtTime(t){return t ? t.slice(0,16) : '-'}
function fmtDate(t){return t ? t.slice(0,10) : ''}
function fmtPlay(min){if(min === null || min === undefined) return '-'; if(min < 60) return min + ' 分钟'; return (min/60).toLocaleString('zh-CN',{maximumFractionDigits:1}) + ' 小时'}
function fmtNum(v){return (v === null || v === undefined) ? '-' : Number(v).toLocaleString('zh-CN')}
function pct(a, b){return b ? (a / b * 100).toFixed(1) + '%' : '0%'}
function toast(msg){const t = $('#toast'); t.textContent = msg; t.classList.add('show'); setTimeout(() => t.classList.remove('show'), 1800)}
function toDateStr(d){return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`}

/* 标签树工具 */
const L2_TO_L1 = {};
TAG_TREE.forEach(n1 => n1.children.forEach(n2 => L2_TO_L1[n2.name] = n1.name));
function findNode(nodes, path){
  const seg = path.split('/');
  let cur = nodes;
  for(let i = 0; i < seg.length; i++){
    const n = cur.find(x => x.name === seg[i]);
    if(!n) return null;
    if(i === seg.length - 1) return n;
    cur = n.children;
  }
  return null;
}
function collectLeafPaths(node, prefix){
  const path = prefix ? prefix + '/' + node.name : node.name;
  if(!node.children || !node.children.length) return [path];
  return node.children.flatMap(ch => collectLeafPaths(ch, path));
}
function parentOf(path){const i = path.lastIndexOf('/'); return i === -1 ? '' : path.slice(0, i)}

/* ---------------- 全局状态 ---------------- */
const state = {
  granularity: 'voice',
  dateStart: '', dateEnd: '',
  games: new Set(), votes: new Set(), sentiments: new Set(),
  tags: new Set(), tagExpanded: new Set(),
  idInput: '', ids: [], idType: 'none',
  page: 1
};
let filtered = VOICES.slice();
function units(){ return state.granularity === 'voice' ? VOICES : OPINIONS; }
function grainName(){ return state.granularity === 'voice' ? '原声报表' : '观点报表'; }

/* ---------------- 多选下拉（支持搜索 + 全选；修复面板内点击不关闭） ---------------- */
function initMS(container, items, allLabel, set){
  const list = container.querySelector('.ms-list');
  const label = container.querySelector('.ms-label');
  const count = container.querySelector('.ms-count');
  const search = container.querySelector('.ms-search input');
  const all = container.querySelector('[data-all]');
  function currentItems(){
    const q = search ? search.value.trim().toLowerCase() : '';
    return q ? items.filter(it => it.label.toLowerCase().includes(q)) : items;
  }
  function render(){
    const cur = currentItems();
    list.innerHTML = cur.map(it =>
      `<label class="ms-opt"><input type="checkbox" value="${esc(it.value)}"><span>${esc(it.label)}</span>${it.count !== undefined ? `<em>${it.count}</em>` : ''}</label>`
    ).join('');
    list.querySelectorAll('input').forEach(i => i.checked = set.has(i.value));
    if(all){
      const vals = cur.map(it => it.value);
      const sel = vals.filter(v => set.has(v)).length;
      all.checked = vals.length > 0 && sel === vals.length;
      all.indeterminate = sel > 0 && sel < vals.length;
    }
    label.textContent = set.size ? `已选 ${set.size} 项` : allLabel;
    count.textContent = set.size ? `${set.size}/${items.length}` : '';
  }
  if(search) search.addEventListener('input', render);
  if(all) all.addEventListener('change', () => {
    const cur = currentItems();
    if(all.checked) cur.forEach(it => set.add(it.value));
    else cur.forEach(it => set.delete(it.value));
    render();
  });
  list.addEventListener('change', e => {
    const cb = e.target.closest('input[type=checkbox]');
    if(!cb) return;
    cb.checked ? set.add(cb.value) : set.delete(cb.value);
    render();
  });
  container.querySelector('.ms-clear').addEventListener('click', () => { set.clear(); render(); });
  render();
  return {render};
}

/* 下拉开合：点 trigger 切换；点面板内部不关闭；点外部关闭所有
   用 composedPath 判断，避免面板内操作重建 DOM 后 target 脱离导致误关 */
document.addEventListener('click', e => {
  const path = e.composedPath();
  const isEl = n => n && n.classList;
  const trigger = path.find(n => isEl(n) && n.classList.contains('ms-trigger'));
  if(trigger){
    const ms = path.find(n => isEl(n) && n.classList.contains('ms'));
    if(ms){
      const was = ms.classList.contains('open');
      $$('.ms.open').forEach(m => m.classList.remove('open'));
      if(!was) ms.classList.add('open');
    }
    return;
  }
  if(!path.some(n => isEl(n) && n.classList.contains('ms'))){
    $$('.ms.open').forEach(m => m.classList.remove('open'));
  }
});

/* ---------------- 时间范围（默认近7日） ---------------- */
function setRangeDays(n){
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - (n - 1));
  state.dateStart = toDateStr(start);
  state.dateEnd = toDateStr(end);
}
function dateLabelText(){
  const s = state.dateStart, e = state.dateEnd;
  if(!s && !e) return '全部时间';
  if(s && e) return `${s} ~ ${e}`;
  if(s) return `${s} 起`;
  return `截至 ${e}`;
}
function refreshDateLabels(){
  $$('[data-ms="date"]').forEach(container => {
    container.querySelector('.ms-label').textContent = dateLabelText();
    container.querySelectorAll('input[data-date]').forEach(i => i.value = state[i.dataset.date === 'start' ? 'dateStart' : 'dateEnd']);
  });
}
function bindDatePickers(){
  $$('[data-ms="date"]').forEach(container => {
    container.querySelectorAll('[data-range]').forEach(b => b.addEventListener('click', () => {
      const r = b.dataset.range;
      if(r === '7d') setRangeDays(7);
      else if(r === '30d') setRangeDays(30);
      else { state.dateStart = ''; state.dateEnd = ''; }
      refreshDateLabels();
      container.classList.remove('open');  // 点快捷按钮后关闭面板
    }));
    container.querySelectorAll('input[data-date]').forEach(i => i.addEventListener('input', () => {
      state[i.dataset.date === 'start' ? 'dateStart' : 'dateEnd'] = i.value;
      refreshDateLabels();
    }));
  });
}

/* ---------------- 标签树 ---------------- */
function searchTagTree(q){
  const out = [];
  TAG_TREE.forEach(n => { const sub = searchNode(n, q); if(sub) out.push(sub); });
  return out;
}
function searchNode(node, q){
  if(node.name.toLowerCase().includes(q)) return cloneFull(node);
  const children = [];
  node.children.forEach(ch => { const sub = searchNode(ch, q); if(sub) children.push(sub); });
  if(children.length) return {name: node.name, count: node.count, children};
  return null;
}
function cloneFull(node){ return {name: node.name, count: node.count, children: node.children.map(cloneFull)}; }
function renderTagNode(node, parentPath, forceExpand){
  const path = parentPath ? parentPath + '/' + node.name : node.name;
  const hasChild = node.children && node.children.length;
  const leafs = hasChild ? collectLeafPaths(node, parentPath) : [path];
  const sel = leafs.filter(p => state.tags.has(p)).length;
  const checked = leafs.length > 0 && sel === leafs.length;
  const expanded = forceExpand || state.tagExpanded.has(path);
  let h = '<div class="tnode">';
  h += '<div class="tnode-row">';
  h += hasChild ? `<button class="tcaret${expanded ? ' open' : ''}" data-toggle="${esc(path)}" type="button">▸</button>` : '<span class="tcaret-sp"></span>';
  h += `<label class="tnode-label"><input type="checkbox" data-node="${esc(path)}"${checked ? ' checked' : ''}> <span>${esc(node.name)}</span><em>${node.count}</em></label>`;
  h += '</div>';
  if(hasChild && expanded) h += '<div class="tnode-children">' + node.children.map(ch => renderTagNode(ch, path, forceExpand)).join('') + '</div>';
  h += '</div>';
  return h;
}
function applyIndeterminate(container){
  container.querySelectorAll('input[data-node]').forEach(cb => {
    const path = cb.dataset.node;
    const node = findNode(TAG_TREE, path);
    if(!node || !node.children || !node.children.length) return;
    const leafs = collectLeafPaths(node, parentOf(path));
    const sel = leafs.filter(p => state.tags.has(p)).length;
    cb.indeterminate = sel > 0 && sel < leafs.length;
  });
}
function renderTagTree(container){
  const q = container.querySelector('.tree-search').value.trim().toLowerCase();
  const tree = q ? searchTagTree(q) : TAG_TREE;
  container.querySelector('.tree-list').innerHTML = tree.length
    ? tree.map(n => renderTagNode(n, '', !!q)).join('')
    : '<div class="tag-empty">无匹配标签</div>';
  applyIndeterminate(container);
  const n = state.tags.size;
  container.querySelector('.ms-label').textContent = n ? `已选 ${n} 项` : '全部标签';
  container.querySelector('.ms-count').textContent = n ? `${n} 项` : '';
}
function syncTagUI(){ $$('[data-ms="tag"]').forEach(c => renderTagTree(c)); }
function bindTagTree(container){
  const search = container.querySelector('.tree-search');
  const list = container.querySelector('.tree-list');
  const all = container.querySelector('[data-all]');
  list.addEventListener('change', e => {
    const cb = e.target.closest('input[data-node]');
    if(!cb) return;
    const path = cb.dataset.node;
    const node = findNode(TAG_TREE, path);
    const leafs = node && node.children && node.children.length ? collectLeafPaths(node, parentOf(path)) : [path];
    if(cb.checked) leafs.forEach(p => state.tags.add(p));
    else leafs.forEach(p => state.tags.delete(p));
    syncTagUI();
  });
  list.addEventListener('click', e => {
    const tg = e.target.closest('[data-toggle]');
    if(!tg) return;
    const path = tg.dataset.toggle;
    state.tagExpanded.has(path) ? state.tagExpanded.delete(path) : state.tagExpanded.add(path);
    syncTagUI();
  });
  search.addEventListener('input', () => syncTagUI());
  all.addEventListener('change', () => {
    const q = search.value.trim().toLowerCase();
    const tree = q ? searchTagTree(q) : TAG_TREE;
    const leafs = tree.flatMap(n => collectLeafPaths(n, ''));
    if(all.checked) leafs.forEach(p => state.tags.add(p));
    else state.tags.clear();
    syncTagUI();
  });
  container.querySelector('.ms-clear').addEventListener('click', () => { state.tags.clear(); syncTagUI(); });
}

/* ---------------- 情感（下拉多选，语义随颗粒度） ---------------- */
function syncSentiScope(){
  const txt = state.granularity === 'voice' ? '整体' : '观点';
  $$('[data-senti-scope]').forEach(s => s.textContent = txt);
}

/* ---------------- ID 筛选 ---------------- */
function parseIds(){
  const raw = state.idInput.trim();
  if(!raw) return { type:'none', ids:[] };
  const tokens = [...new Set(raw.split(/[\s,;，；、\n\r]+/).filter(Boolean))];
  let hasVoice = false, hasOpinion = false;
  tokens.forEach(t => {
    if(VOICE_ID_SET.has(t)) hasVoice = true;
    if(OPINION_ID_SET.has(t)) hasOpinion = true;
  });
  if(hasVoice && hasOpinion) return { type:'mixed', ids:tokens };
  if(hasVoice) return { type:'voice', ids:tokens };
  if(hasOpinion) return { type:'opinion', ids:tokens };
  return { type:'none', ids:tokens };
}
function showIdHint(msg){
  const h = $('[data-id-hint]');
  h.textContent = msg;
  h.classList.add('show');
  setTimeout(() => h.classList.remove('show'), 3000);
}
function matchId(u){
  if(!state.ids.length) return true;
  const idSet = new Set(state.ids);
  if(state.idType === 'mixed') return false;
  if(state.idType === 'voice') return idSet.has(String(u.source_id));
  if(state.idType === 'opinion'){
    return state.granularity === 'voice'
      ? (u.opinions || []).some(op => idSet.has(String(op.id)))
      : idSet.has(String(u.id));
  }
  return false;
}

/* ---------------- UI 同步 ---------------- */
function syncAllUI(){
  refreshDateLabels();
  $$('[data-ms="game"]').forEach(c => c.__ms.render());
  $$('[data-ms="vote"]').forEach(c => c.__ms.render());
  $$('[data-ms="senti"]').forEach(c => c.__ms.render());
  syncTagUI();
  syncSentiScope();
  $$('[data-grain-label]').forEach(s => s.textContent = grainName());
  const gt = $('#grainToggle .gt-label');
  if(gt) gt.textContent = state.granularity === 'voice' ? '观点报表' : '原声报表';
}

/* ---------------- 初始化 ---------------- */
function initFilters(){
  $$('[data-ms="game"]').forEach(c => c.__ms = initMS(c, GAMES.map(g => ({value:g.appid, label:g.name, count:g.count})), '全部游戏', state.games));
  $$('[data-ms="vote"]').forEach(c => c.__ms = initMS(c, [{value:'1', label:'推荐'}, {value:'0', label:'不推荐'}], '全部', state.votes));
  $$('[data-ms="senti"]').forEach(c => c.__ms = initMS(c, [{value:'positive', label:'正向'}, {value:'neutral', label:'中性'}, {value:'negative', label:'负向'}], '全部情感', state.sentiments));
  bindDatePickers();
  $$('[data-ms="tag"]').forEach(c => bindTagTree(c));
  $('[data-id-input]').addEventListener('input', e => { state.idInput = e.target.value; });
  $$('[data-act="apply"]').forEach(b => b.addEventListener('click', applyFilters));
  $$('[data-act="reset"]').forEach(b => b.addEventListener('click', resetFilters));
  setRangeDays(7);
  syncAllUI();
}

/* ---------------- 颗粒度切换 ---------------- */
$('#grainToggle').addEventListener('click', () => {
  state.granularity = state.granularity === 'voice' ? 'opinion' : 'voice';
  state.page = 1;
  computeFiltered();
  renderAll();
  syncAllUI();
  toast(`已切换到${grainName()}（${filtered.length.toLocaleString('zh-CN')} 条）`);
});

/* ---------------- 筛选逻辑 ---------------- */
function unitTagPaths(u){
  if(state.granularity === 'opinion') return [u.path];
  const paths = [];
  if(u.topic) paths.push(u.topic);
  (u.sub_topics || []).forEach(s => { const l1 = L2_TO_L1[s]; paths.push(l1 ? l1 + '/' + s : s); });
  (u.opinions || []).forEach(op => paths.push(op.path));
  return paths;
}
function tagHit(paths){
  if(!state.tags.size) return true;
  const sel = [...state.tags];
  return paths.some(u => sel.some(s => u === s || u.startsWith(s + '/') || s.startsWith(u + '/')));
}
function matchUnit(u){
  const d = fmtDate(u.posted_at);
  if(state.dateStart && d < state.dateStart) return false;
  if(state.dateEnd && d > state.dateEnd) return false;
  if(state.games.size && !state.games.has(u.appid)) return false;
  if(state.votes.size && !state.votes.has(String(u.rating))) return false;
  if(state.sentiments.size && !state.sentiments.has(u.sentiment)) return false;
  if(!tagHit(unitTagPaths(u))) return false;
  if(!matchId(u)) return false;
  return true;
}
function computeFiltered(){ filtered = units().filter(matchUnit); }
function applyFilters(){
  const parsed = parseIds();
  state.ids = parsed.ids;
  state.idType = parsed.type;
  if(parsed.type === 'mixed'){
    filtered = [];
    showIdHint('输入id包含原文和观点，请取其中1种进行查询');
  } else {
    computeFiltered();
  }
  state.page = 1;
  renderAll();
  toast(`筛选完成，共 ${filtered.length.toLocaleString('zh-CN')} 条`);
}
function resetFilters(){
  setRangeDays(7);
  state.games.clear(); state.votes.clear(); state.sentiments.clear(); state.tags.clear();
  state.tagExpanded.clear();
  state.idInput = ''; state.ids = []; state.idType = 'none';
  $('[data-id-input]').value = '';
  computeFiltered();
  state.page = 1;
  syncAllUI();
  renderAll();
  toast('筛选条件已重置');
}
function renderAll(){
  renderDashboard();
  renderList();
}

/* ---------------- URL 参数（?game=appid 跳转自动筛选） ---------------- */
function applyURLParams(){
  const params = new URLSearchParams(location.search);
  const game = params.get('game');
  if(game && GAMES.some(g => g.appid === game)){
    state.games.clear();
    state.games.add(game);
    state.dateStart = ''; state.dateEnd = '';  // 跳转后看全量数据，避免默认近7日查空
    syncAllUI();
  }
}

/* ============================================================
/* ============================================================
   原声列表（右侧抽屉，颗粒度感知）
   ============================================================ */
function summarize(t, limit = 80){
  if(!t) return {text:'', full:'', truncated:false};
  const clean = String(t).replace(/\s+/g, ' ').trim();
  if(clean.length <= limit) return {text: clean, full: clean, truncated: false};
  return {text: clean.slice(0, limit) + '…', full: clean.slice(0, 200), truncated: true};
}
function voiceRowHTML(v){
  const vote = v.rating === 1 ? '<span class="v-vote up">推荐</span>' : '<span class="v-vote down">不推荐</span>';
  const sLabel = SENTI_LABEL[v.sentiment] || v.sentiment || '-';
  const sClass = SENTI_CLASS[v.sentiment] || 't-plain';
  const play = v.playtime_at_review === null || v.playtime_at_review === undefined ? '<b class="nil">-</b>' : `<b>${esc(fmtPlay(v.playtime_at_review))}</b>`;
  const l1 = v.topic;
  const l2s = (v.sub_topics || []).slice(0, 2);
  const tagHtml = (l1 ? `<span class="t-chip t-plain">${esc(l1)}</span>` : '') + l2s.map(s => `<span class="t-chip t-plain">${esc(s)}</span>`).join('');
  const sum = summarize(v.content);
  const textAttr = sum.truncated ? ` data-fulltext="${esc(sum.full)}"` : '';
  return `<div class="vrow" data-id="${v.id}" data-type="voice">
    <div class="v-main">
      <div class="v-top"><span class="v-game">${esc(v.game)}</span><span class="v-time">${esc(fmtTime(v.posted_at))}</span><span class="v-id">#${esc(v.source_id)}</span>${vote}<span class="t-chip ${sClass}">${esc(sLabel)}</span>${tagHtml}</div>
      <div class="v-text${sum.truncated ? ' trunc' : ''}"${textAttr}>${esc(sum.text)}</div>
    </div>
    <div class="v-side">
      <div class="v-stat"><small>游玩</small>${play}</div>
    </div>
  </div>`;
}
function voiceDetailHTML(v){
  const likes = v.likes === null ? '<b class="nil">-</b>' : `<b>${fmtNum(v.likes)}</b>`;
  const replies = v.replies === null ? '<b class="nil">-</b>' : `<b>${fmtNum(v.replies)}</b>`;
  const playForever = v.playtime_forever === null || v.playtime_forever === undefined ? '<b class="nil">-</b>' : `<b>${esc(fmtPlay(v.playtime_forever))}</b>`;
  const ops = (v.opinions || []).map(op => {
    const osLabel = SENTI_LABEL[op.sentiment] || op.sentiment || '-';
    const osClass = SENTI_CLASS[op.sentiment] || 't-plain';
    return `<div class="op">
      <div class="op-head"><span class="t-chip ${osClass}">${esc(osLabel)}</span><span class="op-path">${esc(op.path)}</span></div>
      <div class="op-quote">${esc(op.quote)}</div>
    </div>`;
  }).join('');
  return `<div class="v-detail v-detail-voice" data-detail="${v.id}">
    <div class="vd-row3">
      <div class="vd-cell"><div class="vd-lbl">点赞数</div><div class="vd-val"><b>${likes}</b></div></div>
      <div class="vd-cell"><div class="vd-lbl">回帖数</div><div class="vd-val"><b>${replies}</b></div></div>
      <div class="vd-cell"><div class="vd-lbl">累计游玩时长</div><div class="vd-val"><b>${playForever}</b></div></div>
    </div>
    ${ops ? `<div class="vd-item"><div class="vd-label">观点明细</div>${ops}</div>` : '<div class="vd-item"><div class="vd-label">观点明细</div><div class="vd-quote" style="color:var(--dim)">-</div></div>'}
  </div>`;
}
function opinionRowHTML(o){
  const sLabel = SENTI_LABEL[o.sentiment] || o.sentiment || '-';
  const sClass = SENTI_CLASS[o.sentiment] || 't-plain';
  const play = o.playtime_at_review === null || o.playtime_at_review === undefined ? '<b class="nil">-</b>' : `<b>${esc(fmtPlay(o.playtime_at_review))}</b>`;
  const sum = summarize(o.quote);
  const textAttr = sum.truncated ? ` data-fulltext="${esc(sum.full)}"` : '';
  return `<div class="vrow" data-id="${o.id}" data-type="opinion">
    <div class="v-main">
      <div class="v-top"><span class="v-game">${esc(o.game)}</span><span class="v-time">${esc(fmtTime(o.posted_at))}</span><span class="v-id">#${o.id}</span><span class="t-chip ${sClass}">${esc(sLabel)}</span><span class="t-chip t-plain">${esc(o.path)}</span></div>
      <div class="v-text${sum.truncated ? ' trunc' : ''}"${textAttr}>${esc(sum.text)}</div>
    </div>
    <div class="v-side">
      <div class="v-stat"><small>游玩</small>${play}</div>
    </div>
  </div>`;
}
function opinionDetailHTML(o){
  const vote = o.rating === 1 ? '<span class="v-vote up">推荐</span>' : '<span class="v-vote down">不推荐</span>';
  const osLabel = SENTI_LABEL[o.overall_sentiment] || o.overall_sentiment || '-';
  const osClass = SENTI_CLASS[o.overall_sentiment] || 't-plain';
  const l1 = o.topic;
  const l2s = (o.sub_topics || []).slice(0, 2);
  const tagHtml = (l1 ? `<span class="t-chip t-plain">${esc(l1)}</span>` : '') + l2s.map(s => `<span class="t-chip t-plain">${esc(s)}</span>`).join('');
  const fullContent = (o.content || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>');
  return `<div class="v-detail v-detail-op" data-detail="${o.id}">
    <div class="vd-row3">
      <div class="vd-cell"><div class="vd-lbl">推荐</div><div class="vd-val">${vote}</div></div>
      <div class="vd-cell"><div class="vd-lbl">整体情感</div><div class="vd-val"><span class="t-chip ${osClass}">${esc(osLabel)}</span></div></div>
      <div class="vd-cell"><div class="vd-lbl">整体主题标签</div><div class="vd-val">${tagHtml || '<span style="color:var(--dim)">-</span>'}</div></div>
    </div>
    <div class="vd-item"><div class="vd-label">原声原文</div><div class="vd-quote">${fullContent}</div></div>
  </div>`;
}
function rowHTML(u){ return state.granularity === 'voice' ? voiceRowHTML(u) + voiceDetailHTML(u) : opinionRowHTML(u) + opinionDetailHTML(u); }function renderList(){
  const total = filtered.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if(state.page > pages) state.page = pages;
  const start = (state.page - 1) * PAGE_SIZE;
  const slice = filtered.slice(start, start + PAGE_SIZE);
  const isVoice = state.granularity === 'voice';
  const unitWord = isVoice ? '原声' : '观点';
  // 列表标题随颗粒度切换
  const head = document.querySelector('.rdrawer-top .rdrawer-head span');
  if(head) head.firstChild && (head.firstChild.textContent = isVoice ? '原声列表' : '观点列表');
  $('#vCount').textContent = `· 共 ${total.toLocaleString('zh-CN')} 条${unitWord}`;
  $('#vList').innerHTML = slice.length
    ? slice.map(rowHTML).join('')
    : `<div class="vempty">当前筛选下无数据 · 时间范围为「${dateLabelText()}」，可点「时间」选「全部时间」后查询</div>`;
  renderPager(pages);
}
function renderPager(pages){
  const cur = state.page;
  const nums = new Set([1, pages, cur - 1, cur, cur + 1, cur - 2, cur + 2]);
  const list = [...nums].filter(n => n >= 1 && n <= pages).sort((a, b) => a - b);
  let html = `<button class="page-btn" data-p="${cur - 1}"${cur <= 1 ? ' disabled' : ''}>‹</button>`;
  let prev = 0;
  list.forEach(n => {
    if(n - prev > 1) html += '<button class="page-btn" disabled>…</button>';
    html += `<button class="page-btn${n === cur ? ' active' : ''}" data-p="${n}">${n}</button>`;
    prev = n;
  });
  html += `<button class="page-btn" data-p="${cur + 1}"${cur >= pages ? ' disabled' : ''}>›</button>`;
  $('#vPager').innerHTML = html;
}
$('#vPager').addEventListener('click', e => {
  const btn = e.target.closest('button[data-p]');
  if(!btn || btn.disabled) return;
  state.page = +btn.dataset.p;
  renderList();
});

/* hover 全文：仅当原文被截断（.v-text.trunc）时浮现，上限 200 字 */
const _hoverPop = document.createElement('div');
_hoverPop.className = 'hover-pop';
let _hoverRow = null;
function showHoverPop(row, text){
  if(!text) return;
  _hoverRow = row;
  _hoverPop.innerHTML = text;
  row.appendChild(_hoverPop);
  _hoverPop.classList.add('show');
}
function hideHoverPop(){
  _hoverPop.classList.remove('show');
  if(_hoverPop.parentNode) _hoverPop.parentNode.removeChild(_hoverPop);
  _hoverRow = null;
}
$('#vList').addEventListener('mouseover', e => {
  const txt = e.target.closest('.v-text.trunc');
  if(!txt) return;
  const row = txt.closest('.vrow');
  if(!row || row === _hoverRow) return;
  hideHoverPop();
  showHoverPop(row, txt.dataset.fulltext);
});
$('#vList').addEventListener('mouseout', e => {
  if(!_hoverRow) return;
  const related = e.relatedTarget;
  if(related && _hoverRow.contains(related)) return;
  hideHoverPop();
});

/* 详情展开（行内 accordion） */
$('#vList').addEventListener('click', e => {
  const row = e.target.closest('.vrow');
  if(!row) return;
  const detail = row.nextElementSibling;
  if(detail && detail.classList.contains('v-detail')){
    detail.classList.toggle('open');
    row.classList.toggle('expanded');
  }
});

/* ---------------- 右侧抽屉折叠 + 高度调节 ---------------- */
function bindDrawers(){
  // 仅 AI 抽屉可折叠；原声列表不可折叠
  $$('[data-toggle="ai"]').forEach(head => {
    head.addEventListener('click', () => {
      const drawer = head.closest('.rdrawer');
      const isOpen = drawer.classList.toggle('open');
      const caret = head.querySelector('.rcaret');
      if(caret) caret.classList.toggle('up', isOpen);
    });
  });
}

/* ---------------- 导出 CSV ---------------- */
function csvCell(x){
  const s = String(x ?? '');
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
$('#vExport').addEventListener('click', () => {
  const isVoice = state.granularity === 'voice';
  const header = isVoice
    ? ['原声ID','游戏','发布时间','推荐','整体情感','情感分数','L1标签','L2标签','观点数','评论时游玩(小时)','点赞','回帖','评论内容']
    : ['观点ID','所属原声ID','标签路径','观点情感','游戏','发布时间','推荐','所属评论整体情感','观点原文'];
  const rows = filtered.map(u => isVoice ? [
    u.source_id, u.game, fmtTime(u.posted_at), u.rating === 1 ? '推荐' : '不推荐',
    SENTI_LABEL[u.sentiment] || u.sentiment || '', u.sentiment_score ?? '',
    u.topic || '', (u.sub_topics || []).join('|'), (u.opinions || []).length,
    u.playtime_at_review === null ? '' : (u.playtime_at_review / 60).toFixed(1),
    u.likes ?? '', u.replies ?? '', u.content || ''
  ] : [
    u.id, u.source_id, u.path, SENTI_LABEL[u.sentiment] || u.sentiment,
    u.game, fmtTime(u.posted_at), u.rating === 1 ? '推荐' : '不推荐',
    SENTI_LABEL[u.overall_sentiment] || u.overall_sentiment || '', u.quote || ''
  ]);
  const csv = '\ufeff' + [header, ...rows].map(r => r.map(csvCell).join(',')).join('\r\n');
  const blob = new Blob([csv], {type:'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `lynx_${isVoice ? 'voices' : 'opinions'}_${new Date().toISOString().slice(0,10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast(`已导出 ${filtered.length.toLocaleString('zh-CN')} 条`);
});

/* ---------------- 启动 ---------------- */
initFilters();
bindDrawers();
applyURLParams();
computeFiltered();
/* 首次渲染在 dashboard.js 末尾执行（chartState 初始化后，避免 TDZ） */
