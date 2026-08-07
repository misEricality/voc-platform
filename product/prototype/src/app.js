/* 声镜 VOC 原型 —— 全页面脚本 v3
   共享筛选器（数据看板 / 原声列表 双页联动）+ 原声列表 + 详情抽屉 + 导出
   注：dashboard.js 由构建脚本拼接在本文件末尾（同一 <script> 作用域） */
const DATA = /*__DATA__*/ null;
const VOICES = DATA.voices, GAMES = DATA.games, TAG_TREE = DATA.tags;
const PAGE_SIZE = 20;

/* ---------------- 工具 ---------------- */
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
function esc(s){return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
const SENTI_LABEL = {positive:'正向', neutral:'中性', negative:'负向'};
const SENTI_CLASS = {positive:'t-pos', neutral:'t-neu', negative:'t-neg'};
const LANG_LABEL = {schinese:'简体中文', tchinese:'繁体中文', english:'英语'};
function fmtTime(t){return t ? t.slice(0,16) : '-'}
function fmtDate(t){return t ? t.slice(0,10) : ''}
function fmtPlay(min){if(min === null || min === undefined) return '-'; if(min < 60) return min + ' 分钟'; return (min/60).toLocaleString('zh-CN',{maximumFractionDigits:1}) + ' 小时'}
function fmtNum(v){return (v === null || v === undefined) ? '-' : Number(v).toLocaleString('zh-CN')}
function pct(a, b){return b ? (a / b * 100).toFixed(1) + '%' : '0%'}
function parseKw(s){return s.split(/[,，、;；]+/).map(x => x.trim().toLowerCase()).filter(Boolean)}
function toast(msg){const t = $('#toast'); t.textContent = msg; t.classList.add('show'); setTimeout(() => t.classList.remove('show'), 1800)}

/* ---------------- 共享筛选状态（看板 / 列表同一套） ---------------- */
const state = {
  dateStart:'', dateEnd:'',
  games:new Set(), votes:new Set(), sentiments:new Set(),
  sentiMode:'overall',
  l1:new Set(), l2:new Set(), l3:new Set(),   // l2/l3 存完整路径
  page:1
};
let filtered = VOICES.slice();
let activePage = 'dashboard';

/* ---------------- 多选下拉组件（支持多实例） ---------------- */
function initMS(container, items, allLabel, set){
  const list = container.querySelector('.ms-list');
  const label = container.querySelector('.ms-label');
  const count = container.querySelector('.ms-count');
  list.innerHTML = items.map(it =>
    `<label class="ms-opt"><input type="checkbox" value="${esc(it.value)}"><span>${esc(it.label)}</span>${it.count !== undefined ? `<em>${it.count}</em>` : ''}</label>`
  ).join('');
  function refresh(){
    list.querySelectorAll('input').forEach(i => i.checked = set.has(i.value));
    label.textContent = set.size ? `已选 ${set.size} 项` : allLabel;
    count.textContent = set.size ? `${set.size}/${items.length}` : '';
  }
  list.addEventListener('change', e => {
    const cb = e.target.closest('input[type=checkbox]');
    if(!cb) return;
    cb.checked ? set.add(cb.value) : set.delete(cb.value);
    refresh();
    syncSentiAvailability();
  });
  container.querySelector('.ms-clear').addEventListener('click', () => {
    set.clear(); refresh();
  });
  refresh();
  return {refresh};
}

/* 下拉开合（全局委托：点击 .ms 内部切换，外部关闭） */
document.addEventListener('click', e => {
  const ms = e.target.closest('.ms');
  if(ms){
    if(ms.classList.contains('disabled')) return;
    const was = ms.classList.contains('open');
    $$('.ms.open').forEach(m => m.classList.remove('open'));
    if(!was) ms.classList.add('open');
  } else {
    $$('.ms.open').forEach(m => m.classList.remove('open'));
  }
});

/* ---------------- 标签级联组件（支持多实例，共享 state） ---------------- */
function tagOptionHTML(level, path, name, count, checked){
  return `<label class="ms-opt"><input type="checkbox" data-level="${level}" value="${esc(path)}"${checked ? ' checked' : ''}><span>${esc(name)}</span><em>${count}</em></label>`;
}
function renderTagCascade(container){
  const l1el = container.querySelector('[data-tag-col="1"]');
  const l2el = container.querySelector('[data-tag-col="2"]');
  const l3el = container.querySelector('[data-tag-col="3"]');
  l1el.innerHTML = TAG_TREE.map(n => tagOptionHTML(1, n.name, n.name, n.count, state.l1.has(n.name))).join('');
  const l1Nodes = state.l1.size ? TAG_TREE.filter(n => state.l1.has(n.name)) : TAG_TREE;
  const l2items = [];
  l1Nodes.forEach(n1 => n1.children.forEach(n2 => l2items.push({path:n1.name + '/' + n2.name, node:n2})));
  l2el.innerHTML = l2items.length
    ? l2items.map(it => tagOptionHTML(2, it.path, it.node.name, it.node.count, state.l2.has(it.path))).join('')
    : '<div class="tag-empty">请先选择一级标签</div>';
  const l2Sel = state.l2.size ? l2items.filter(it => state.l2.has(it.path)) : l2items;
  const l3items = [];
  l2Sel.forEach(it => it.node.children.forEach(n3 => l3items.push({path:it.path + '/' + n3.name, node:n3})));
  l3el.innerHTML = l3items.length
    ? l3items.map(it => tagOptionHTML(3, it.path, it.node.name, it.node.count, state.l3.has(it.path))).join('')
    : '<div class="tag-empty">暂无可选三级标签</div>';
  const n = state.l1.size + state.l2.size + state.l3.size;
  container.querySelector('.ms-label').textContent = n ? `已选 ${n} 项` : '全部标签';
  container.querySelector('.ms-count').textContent = n ? `${n} 项` : '';
}
function bindTagCascade(container){
  container.querySelector('.ms-pop').addEventListener('change', e => {
    const cb = e.target.closest('input[type=checkbox]');
    if(!cb) return;
    const level = +cb.dataset.level, path = cb.value;
    const set = level === 1 ? state.l1 : level === 2 ? state.l2 : state.l3;
    if(cb.checked){
      set.add(path);
    } else {
      set.delete(path);
      if(level === 1){
        [...state.l2].forEach(p => { if(p.startsWith(path + '/')) state.l2.delete(p) });
        [...state.l3].forEach(p => { if(p.startsWith(path + '/')) state.l3.delete(p) });
      }
      if(level === 2){
        [...state.l3].forEach(p => { if(p.startsWith(path + '/')) state.l3.delete(p) });
      }
    }
    syncAllUI();
  });
  container.querySelector('.ms-clear').addEventListener('click', () => {
    state.l1.clear(); state.l2.clear(); state.l3.clear(); syncAllUI();
  });
}

/* ---------------- 情感模式切换 ---------------- */
function bindSentiSeg(container){
  container.addEventListener('click', e => {
    const btn = e.target.closest('button');
    if(!btn) return;
    state.sentiMode = btn.dataset.mode;
    syncAllUI();
  });
}
function syncSentiAvailability(){
  const needTag = state.sentiMode === 'tag';
  const noTag = !(state.l1.size || state.l2.size || state.l3.size);
  $$('[data-ms="senti"]').forEach(ms => ms.classList.toggle('disabled', needTag && noTag));
  $$('.seg-hint').forEach(h => h.classList.toggle('show', needTag && noTag));
}

/* ---------------- 全量 UI 同步（state → 两页控件） ---------------- */
function syncAllUI(){
  $$('input[data-date]').forEach(i => i.value = state[i.dataset.date === 'start' ? 'dateStart' : 'dateEnd']);
  $$('input[data-kw]').forEach(i => {
    const k = {and:'kwAnd', or:'kwOr', not:'kwNot'}[i.dataset.kw];
    i.value = state[k].join(', ');
  });
  $$('[data-ms="game"]').forEach(ms => msGame.refresh());
  $$('[data-ms="vote"]').forEach(ms => msVote.refresh());
  $$('[data-ms="senti"]').forEach(ms => msSenti.refresh());
  $$('[data-ms="tag"]').forEach(c => renderTagCascade(c));
  $$('.senti-seg').forEach(seg => seg.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.mode === state.sentiMode)));
  syncSentiAvailability();
}

/* ---------------- 筛选器初始化（遍历两页） ---------------- */
const msGame = {refresh(){ $$('[data-ms="game"]').forEach(c => c.__ms.refresh()) }};
const msVote = {refresh(){ $$('[data-ms="vote"]').forEach(c => c.__ms.refresh()) }};
const msSenti = {refresh(){ $$('[data-ms="senti"]').forEach(c => c.__ms.refresh()) }};
function initFilters(){
  $$('[data-ms="game"]').forEach(c => c.__ms = initMS(c, GAMES.map(g => ({value:g.appid, label:g.name, count:g.count})), '全部游戏', state.games));
  $$('[data-ms="vote"]').forEach(c => c.__ms = initMS(c, [{value:'1', label:'推荐'}, {value:'0', label:'不推荐'}], '全部', state.votes));
  $$('[data-ms="senti"]').forEach(c => c.__ms = initMS(c, [{value:'positive', label:'正向'}, {value:'neutral', label:'中性'}, {value:'negative', label:'负向'}], '全部情感', state.sentiments));
  $$('[data-ms="tag"]').forEach(c => { renderTagCascade(c); bindTagCascade(c); });
  $$('.senti-seg').forEach(bindSentiSeg);
  $$('input[data-date]').forEach(i => i.addEventListener('input', () => {
    state[i.dataset.date === 'start' ? 'dateStart' : 'dateEnd'] = i.value;
  }));
  $$('input[data-kw]').forEach(i => i.addEventListener('input', () => {
    const k = {and:'kwAnd', or:'kwOr', not:'kwNot'}[i.dataset.kw];
    state[k] = parseKw(i.value);
  }));
  $$('[data-act="apply"]').forEach(b => b.addEventListener('click', applyFilters));
  $$('[data-act="reset"]').forEach(b => b.addEventListener('click', resetFilters));
  syncAllUI();
}

/* ---------------- 筛选逻辑（分层取数） ---------------- */
function selectedTagPrefixes(){
  return [...state.l1, ...state.l2, ...state.l3];
}
function matchVoice(v){
  const d = fmtDate(v.posted_at);
  if(state.dateStart && d < state.dateStart) return false;
  if(state.dateEnd && d > state.dateEnd) return false;
  if(state.games.size && !state.games.has(v.appid)) return false;
  if(state.votes.size && !state.votes.has(String(v.rating))) return false;
  if(state.l1.size && !state.l1.has(v.topic)) return false;
  if(state.l2.size){
    const l2names = new Set([...state.l2].map(p => p.split('/')[1]));
    if(!(v.sub_topics || []).some(s => l2names.has(s))) return false;
  }
  if(state.l3.size && !(v.opinions || []).some(op => state.l3.has(op.path))) return false;
  if(state.sentiMode === 'overall'){
    if(state.sentiments.size && !state.sentiments.has(v.sentiment)) return false;
  } else {
    const prefixes = selectedTagPrefixes();
    if(prefixes.length){
      const ops = (v.opinions || []).filter(op => prefixes.some(p => op.path === p || op.path.startsWith(p + '/')));
      if(state.sentiments.size){
        if(!ops.some(op => state.sentiments.has(op.sentiment))) return false;
      } else if(!ops.length) return false;
    }
  }
  const text = (v.content || '').toLowerCase();
  if(state.kwAnd.length && !state.kwAnd.every(k => text.includes(k))) return false;
  if(state.kwOr.length && !state.kwOr.some(k => text.includes(k))) return false;
  if(state.kwNot.length && state.kwNot.some(k => text.includes(k))) return false;
  return true;
}
function applyFilters(){
  filtered = VOICES.filter(matchVoice);
  state.page = 1;
  renderActive();
  toast(`筛选完成，共 ${filtered.length.toLocaleString('zh-CN')} 条`);
}
function resetFilters(){
  state.dateStart = ''; state.dateEnd = '';
  state.games.clear(); state.votes.clear(); state.sentiments.clear();
  state.sentiMode = 'overall';
  state.l1.clear(); state.l2.clear(); state.l3.clear();
  filtered = VOICES.slice();
  state.page = 1;
  syncAllUI();
  renderActive();
  toast('筛选条件已重置');
}
function renderActive(){
  if(activePage === 'voices') renderList();
  else renderDashboard();
}

/* ---------------- 导航 ---------------- */
document.querySelectorAll('.nav-btn').forEach(btn => btn.onclick = () => {
  document.querySelectorAll('.nav-btn,.page').forEach(x => x.classList.remove('active'));
  btn.classList.add('active');
  activePage = btn.dataset.page;
  document.getElementById(activePage).classList.add('active');
  syncAllUI();
  renderActive();
});

/* ============================================================
   原声列表
   ============================================================ */
function tagChips(v){
  const chips = [];
  if(v.opinions && v.opinions.length){
    const seen = new Set();
    v.opinions.forEach(op => {
      const leaf = op.path.split('/').pop();
      if(seen.has(leaf)) return;
      seen.add(leaf);
      chips.push(`<span class="t-chip ${SENTI_CLASS[op.sentiment] || 't-plain'}">${esc(leaf)}</span>`);
    });
  } else {
    if(v.topic) chips.push(`<span class="t-chip t-plain">${esc(v.topic)}</span>`);
    (v.sub_topics || []).forEach(s => chips.push(`<span class="t-chip t-plain">${esc(s)}</span>`));
  }
  const MAX = 5;
  if(chips.length > MAX){
    const more = chips.length - MAX + 1;
    return chips.slice(0, MAX - 1).join('') + `<span class="t-chip t-more">+${more}</span>`;
  }
  return chips.join('');
}
function rowHTML(v){
  const vote = v.rating === 1 ? '<span class="v-vote up">推荐</span>' : '<span class="v-vote down">不推荐</span>';
  const play = v.playtime_at_review === null || v.playtime_at_review === undefined
    ? '<b class="nil">-</b>' : `<b>${esc(fmtPlay(v.playtime_at_review))}</b>`;
  const likes = v.likes === null ? '<b class="nil">-</b>' : `<b>${fmtNum(v.likes)}</b>`;
  const replies = v.replies === null ? '<b class="nil">-</b>' : `<b>${fmtNum(v.replies)}</b>`;
  return `<div class="vrow" data-id="${v.id}">
    <div class="v-main">
      <div class="v-top"><span class="v-game">${esc(v.game)}</span><span class="v-time">${esc(fmtTime(v.posted_at))}</span>${vote}</div>
      <div class="v-text">${esc(v.content)}</div>
      <div class="v-tags">${tagChips(v)}</div>
    </div>
    <div class="v-side">
      <div class="v-stat"><small>评论时游玩</small>${play}</div>
      <div class="v-stat"><small>点赞</small>${likes}</div>
      <div class="v-stat"><small>回帖</small>${replies}</div>
    </div>
  </div>`;
}
function renderList(){
  const total = filtered.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if(state.page > pages) state.page = pages;
  const start = (state.page - 1) * PAGE_SIZE;
  const slice = filtered.slice(start, start + PAGE_SIZE);
  $('#vMeta').textContent = `共 ${total.toLocaleString('zh-CN')} 条 · 第 ${state.page}/${pages} 页`;
  $('#vList').innerHTML = slice.length
    ? slice.map(rowHTML).join('')
    : '<div class="vempty">没有匹配的原声，请调整筛选条件</div>';
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
  $('#voices').scrollIntoView({behavior:'smooth', block:'start'});
});

/* 悬浮全文 */
const textTip = $('#textTip');
$('#vList').addEventListener('mouseover', e => {
  const t = e.target.closest('.v-text');
  if(!t) return;
  textTip.textContent = t.textContent;
  textTip.style.display = 'block';
  const r = t.getBoundingClientRect();
  const tipW = Math.min(600, window.innerWidth - 40);
  let left = Math.min(r.left, window.innerWidth - tipW - 20);
  let top = r.bottom + 8;
  if(top + textTip.offsetHeight > window.innerHeight - 12) top = Math.max(12, r.top - textTip.offsetHeight - 8);
  textTip.style.left = Math.max(12, left) + 'px';
  textTip.style.top = top + 'px';
});
$('#vList').addEventListener('mouseout', e => {
  if(e.target.closest('.v-text')) textTip.style.display = 'none';
});

/* 详情抽屉 */
function openDetail(id){
  const v = filtered.find(x => x.id === id) || VOICES.find(x => x.id === id);
  if(!v) return;
  $('#d2Game').textContent = v.game;
  $('#d2Meta').innerHTML =
    (v.rating === 1 ? '<span class="v-vote up">推荐</span>' : '<span class="v-vote down">不推荐</span>') +
    `<span>${esc(fmtTime(v.posted_at))}</span><span>作者 ${esc(v.author)}</span>`;
  $('#d2Quote').textContent = v.content;
  const sLabel = SENTI_LABEL[v.sentiment] || v.sentiment || '-';
  const sClass = SENTI_CLASS[v.sentiment] || 't-plain';
  $('#d2Senti').innerHTML =
    `<span class="t-chip ${sClass}">${esc(sLabel)}</span>` +
    `<span class="d2-score">情感分数 <b>${v.sentiment_score === null ? '-' : Number(v.sentiment_score).toFixed(2)}</b></span>` +
    `<span class="d2-score">置信度 <b>${v.sentiment_confidence === null ? '-' : (Number(v.sentiment_confidence) * 100).toFixed(0) + '%'}</b></span>`;
  const tags = [];
  if(v.topic) tags.push(`<span class="t-chip t-plain">L1 · ${esc(v.topic)}</span>`);
  (v.sub_topics || []).forEach(s => tags.push(`<span class="t-chip t-plain">L2 · ${esc(s)}</span>`));
  $('#d2Tags').innerHTML = tags.join('') || '<span style="color:#a3afbd;font-size:13px">-</span>';
  const opSec = $('#d2OpSec');
  if(v.opinions && v.opinions.length){
    opSec.style.display = '';
    $('#d2Ops').innerHTML = v.opinions.map(op =>
      `<div class="op"><div class="op-head"><span class="op-path">${esc(op.path)}</span><span class="t-chip ${SENTI_CLASS[op.sentiment] || 't-plain'}">${esc(SENTI_LABEL[op.sentiment] || op.sentiment)}</span></div><div class="op-quote">${esc(op.quote)}</div></div>`
    ).join('');
  } else {
    opSec.style.display = 'none';
  }
  $('#d2Stats').innerHTML = [
    ['评论时游玩时长', fmtPlay(v.playtime_at_review)],
    ['累计游玩时长', fmtPlay(v.playtime_forever)],
    ['点赞数', fmtNum(v.likes)],
    ['回帖数', fmtNum(v.replies)]
  ].map(([k, val]) => `<div class="d2-item"><small>${k}</small><b>${esc(val)}</b></div>`).join('');
  const wvs = v.weighted_vote_score ? Number(v.weighted_vote_score).toFixed(2) : '-';
  $('#d2Src').innerHTML = [
    ['作者 Steam64', v.author || '-'],
    ['语言', LANG_LABEL[v.language] || v.language || '-'],
    ['AppID', v.appid],
    ['评论 ID', v.source_id || '-'],
    ['加权投票分', wvs],
    ['数据来源', 'Steam 官方 API']
  ].map(([k, val]) => `<div class="d2-item"><small>${k}</small><b>${esc(val)}</b></div>`).join('');
  const badges = [];
  if(v.refunded) badges.push('<span class="badge badge-danger">已退款</span>');
  if(v.early_access) badges.push('<span class="badge badge-warn">抢先体验</span>');
  if(v.steam_deck) badges.push('<span class="badge badge-soft">Steam Deck</span>');
  if(v.received_for_free) badges.push('<span class="badge">免费获取</span>');
  $('#d2Badges').innerHTML = badges.join('') || '<span style="color:#a3afbd;font-size:12px">无特殊标记</span>';
  $('#d2Dev').textContent = '-';
  $('#d2Mask').classList.add('open');
}
$('#vList').addEventListener('click', e => {
  const row = e.target.closest('.vrow');
  if(row) openDetail(+row.dataset.id);
});
$('#d2Close').addEventListener('click', () => $('#d2Mask').classList.remove('open'));
$('#d2Mask').addEventListener('click', e => { if(e.target.id === 'd2Mask') e.target.classList.remove('open') });
document.addEventListener('keydown', e => { if(e.key === 'Escape') $('#d2Mask').classList.remove('open') });

/* 导出 CSV */
function csvCell(x){
  const s = String(x ?? '');
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
$('#vExport').addEventListener('click', () => {
  const header = ['评论ID','游戏','AppID','发布时间','推荐','整体情感','情感分数','置信度','L1标签','L2标签','观点数','评论时游玩(小时)','累计游玩(小时)','点赞','回帖','作者','评论内容'];
  const rows = filtered.map(v => [
    v.id, v.game, v.appid, fmtTime(v.posted_at),
    v.rating === 1 ? '推荐' : '不推荐',
    SENTI_LABEL[v.sentiment] || v.sentiment || '',
    v.sentiment_score ?? '', v.sentiment_confidence ?? '',
    v.topic || '', (v.sub_topics || []).join('|'), (v.opinions || []).length,
    v.playtime_at_review === null ? '' : (v.playtime_at_review / 60).toFixed(1),
    v.playtime_forever === null ? '' : (v.playtime_forever / 60).toFixed(1),
    v.likes ?? '', v.replies ?? '', v.author || '', v.content || ''
  ]);
  const csv = '\ufeff' + [header, ...rows].map(r => r.map(csvCell).join(',')).join('\r\n');
  const blob = new Blob([csv], {type:'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `voc_voices_${new Date().toISOString().slice(0,10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast(`已导出 ${filtered.length.toLocaleString('zh-CN')} 条`);
});

/* ---------------- 启动 ---------------- */
initFilters();
renderDashboard();
