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

/* ============================================================
   原声列表（右侧抽屉，颗粒度感知）
   ============================================================ */
function voiceRowHTML(v){
  const vote = v.rating === 1 ? '<span class="v-vote up">推荐</span>' : '<span class="v-vote down">不推荐</span>';
  const play = v.playtime_at_review === null || v.playtime_at_review === undefined ? '<b class="nil">-</b>' : `<b>${esc(fmtPlay(v.playtime_at_review))}</b>`;
  const likes = v.likes === null ? '<b class="nil">-</b>' : `<b>${fmtNum(v.likes)}</b>`;
  const replies = v.replies === null ? '<b class="nil">-</b>' : `<b>${fmtNum(v.replies)}</b>`;
  const chips = [];
  if(v.opinions && v.opinions.length){
    const seen = new Set();
    v.opinions.forEach(op => { const leaf = op.path.split('/').pop(); if(seen.has(leaf)) return; seen.add(leaf); chips.push(`<span class="t-chip ${SENTI_CLASS[op.sentiment] || 't-plain'}">${esc(leaf)}</span>`); });
  } else {
    if(v.topic) chips.push(`<span class="t-chip t-plain">${esc(v.topic)}</span>`);
    (v.sub_topics || []).forEach(s => chips.push(`<span class="t-chip t-plain">${esc(s)}</span>`));
  }
  const MAX = 4;
  const tagHtml = chips.length > MAX ? chips.slice(0, MAX - 1).join('') + `<span class="t-chip t-more">+${chips.length - MAX + 1}</span>` : chips.join('');
  return `<div class="vrow" data-id="${v.id}" data-type="voice">
    <div class="v-main">
      <div class="v-top"><span class="v-game">${esc(v.game)}</span><span class="v-time">${esc(fmtTime(v.posted_at))}</span>${vote}<span class="v-id">#${esc(v.source_id)}</span></div>
      <div class="v-text">${esc(v.content)}</div>
      <div class="v-tags">${tagHtml}</div>
    </div>
    <div class="v-side">
      <div class="v-stat"><small>游玩</small>${play}</div>
      <div class="v-stat"><small>点赞</small>${likes}</div>
      <div class="v-stat"><small>回帖</small>${replies}</div>
    </div>
  </div>`;
}
function voiceDetailHTML(v){
  const sLabel = SENTI_LABEL[v.sentiment] || v.sentiment || '-';
  const sClass = SENTI_CLASS[v.sentiment] || 't-plain';
  const tags = [];
  if(v.topic) tags.push(`<span class="t-chip t-plain">L1 · ${esc(v.topic)}</span>`);
  (v.sub_topics || []).forEach(s => tags.push(`<span class="t-chip t-plain">L2 · ${esc(s)}</span>`));
  const ops = (v.opinions || []).map(op =>
    `<div class="op"><div class="op-head"><span class="op-path">${esc(op.path)}</span><span class="t-chip ${SENTI_CLASS[op.sentiment] || 't-plain'}">${esc(SENTI_LABEL[op.sentiment] || op.sentiment)}</span></div><div class="op-quote">${esc(op.quote)}</div></div>`
  ).join('');
  const badges = [];
  if(v.refunded) badges.push('<span class="badge badge-danger">已退款</span>');
  if(v.early_access) badges.push('<span class="badge badge-warn">抢先体验</span>');
  if(v.steam_deck) badges.push('<span class="badge badge-soft">Steam Deck</span>');
  if(v.received_for_free) badges.push('<span class="badge">免费获取</span>');
  return `<div class="v-detail" data-detail="${v.id}">
    <div class="vd-item"><div class="vd-label">整体情感</div><div class="vd-chips"><span class="t-chip ${sClass}">${esc(sLabel)}</span><span class="t-chip t-plain">分数 ${v.sentiment_score === null ? '-' : Number(v.sentiment_score).toFixed(2)}</span></div></div>
    <div class="vd-item"><div class="vd-label">主题标签</div><div class="vd-chips">${tags.join('') || '<span style="color:var(--dim)">-</span>'}</div></div>
    ${ops ? `<div class="vd-item"><div class="vd-label">观点明细</div>${ops}</div>` : ''}
    <div class="vd-item"><div class="vd-grid"><div class="vd-cell"><small>累计游玩</small><b>${esc(fmtPlay(v.playtime_forever))}</b></div><div class="vd-cell"><small>Steam 标记</small><b>${badges.join('') || '-'}</b></div></div></div>
  </div>`;
}
function opinionRowHTML(o){
  const vote = o.rating === 1 ? '<span class="v-vote up">推荐</span>' : '<span class="v-vote down">不推荐</span>';
  const play = o.playtime_at_review === null || o.playtime_at_review === undefined ? '<b class="nil">-</b>' : `<b>${esc(fmtPlay(o.playtime_at_review))}</b>`;
  const likes = o.likes === null ? '<b class="nil">-</b>' : `<b>${fmtNum(o.likes)}</b>`;
  const replies = o.replies === null ? '<b class="nil">-</b>' : `<b>${fmtNum(o.replies)}</b>`;
  return `<div class="vrow" data-id="${o.id}" data-type="opinion">
    <div class="v-main">
      <div class="v-top"><span class="v-game">${esc(o.game)}</span><span class="v-time">${esc(fmtTime(o.posted_at))}</span>${vote}<span class="v-id">#${o.id}</span></div>
      <div class="v-text">${esc(o.quote)}</div>
      <div class="v-tags"><span class="t-chip ${SENTI_CLASS[o.sentiment] || 't-plain'}">${esc(o.path)}</span></div>
    </div>
    <div class="v-side">
      <div class="v-stat"><small>游玩</small>${play}</div>
      <div class="v-stat"><small>点赞</small>${likes}</div>
      <div class="v-stat"><small>回帖</small>${replies}</div>
    </div>
  </div>`;
}
function opinionDetailHTML(o){
  const badges = [];
  if(o.refunded) badges.push('<span class="badge badge-danger">已退款</span>');
  if(o.early_access) badges.push('<span class="badge badge-warn">抢先体验</span>');
  if(o.steam_deck) badges.push('<span class="badge badge-soft">Steam Deck</span>');
  if(o.received_for_free) badges.push('<span class="badge">免费获取</span>');
  const tags = [];
  if(o.topic) tags.push(`<span class="t-chip t-plain">L1 · ${esc(o.topic)}</span>`);
  (o.sub_topics || []).forEach(s => tags.push(`<span class="t-chip t-plain">L2 · ${esc(s)}</span>`));
  return `<div class="v-detail" data-detail="${o.id}">
    <div class="vd-item"><div class="vd-label">所属评论 · 整体情感</div><div class="vd-chips"><span class="t-chip ${SENTI_CLASS[o.overall_sentiment] || 't-plain'}">${esc(SENTI_LABEL[o.overall_sentiment] || o.overall_sentiment || '-')}</span></div></div>
    <div class="vd-item"><div class="vd-label">所属评论原声</div><div class="vd-quote">${esc(o.content)}</div></div>
    <div class="vd-item"><div class="vd-label">主题标签</div><div class="vd-chips">${tags.join('') || '<span style="color:var(--dim)">-</span>'}</div></div>
    <div class="vd-item"><div class="vd-grid"><div class="vd-cell"><small>累计游玩</small><b>${esc(fmtPlay(o.playtime_forever))}</b></div><div class="vd-cell"><small>Steam 标记</small><b>${badges.join('') || '-'}</b></div></div></div>
  </div>`;
}
function rowHTML(u){ return state.granularity === 'voice' ? voiceRowHTML(u) + voiceDetailHTML(u) : opinionRowHTML(u) + opinionDetailHTML(u); }
function renderList(){
  const total = filtered.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if(state.page > pages) state.page = pages;
  const start = (state.page - 1) * PAGE_SIZE;
  const slice = filtered.slice(start, start + PAGE_SIZE);
  const unitWord = state.granularity === 'voice' ? '原声' : '观点';
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
  $$('[data-toggle]').forEach(head => {
    head.addEventListener('click', () => {
      const drawer = head.closest('.rdrawer');
      const caret = head.querySelector('.rcaret');
      if(drawer.dataset.drawer === 'list'){
        drawer.classList.toggle('collapsed');
        if(caret) caret.classList.toggle('up', drawer.classList.contains('collapsed'));
      } else {
        drawer.classList.toggle('open');
        if(caret) caret.classList.toggle('up', drawer.classList.contains('open'));
        const body = drawer.querySelector('.rdrawer-body');
        if(drawer.classList.contains('open') && body) body.style.height = '';  // 回到默认 33vh
      }
    });
  });
  initResize();
}
function initResize(){
  const resize = $('[data-resize]');
  if(!resize) return;
  const drawer = resize.closest('.rdrawer');
  const body = drawer.querySelector('.rdrawer-body');
  let dragging = false;
  resize.addEventListener('mousedown', e => {
    e.preventDefault();
    dragging = true;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'ns-resize';
  });
  document.addEventListener('mousemove', e => {
    if(!dragging) return;
    const bottom = window.innerHeight - 40;  // 页脚高度
    let h = bottom - e.clientY - 6;
    const minH = Math.round(window.innerHeight * 0.33);
    const maxH = Math.round(window.innerHeight * 0.66);
    h = Math.max(minH, Math.min(maxH, h));
    body.style.height = h + 'px';
  });
  document.addEventListener('mouseup', () => {
    if(!dragging) return;
    dragging = false;
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
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
computeFiltered();
/* 首次渲染在 dashboard.js 末尾执行（chartState 初始化后，避免 TDZ） */
