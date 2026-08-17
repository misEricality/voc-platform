/* ============================================================
   数据看板 —— 图表渲染（拼接至 app.js 之后，共享全局）
   颗粒度感知 + 悬停下钻 + 折线/堆叠条形/柱形
   ============================================================ */

const SERIES_COLORS = ['#66c0f4', '#a1cd44', '#e05c5c', '#d9b54e', '#4f94cd'];
const chartState = {
  daily: { mode: 'day', byGame: false },
  praise: { mode: 'day', byGame: false },
  sentiment: { byGame: false },
  topic: { level: 'L1', byGame: false, drill: [] }
};

function dEmpty(msg){ return `<div class="dempty">${msg || '当前筛选下无数据'}</div>`; }
function gameName(appid){ const g = GAMES.find(x => x.appid === appid); return g ? g.name : appid; }
function topGames(n = 5){
  const cnt = {};
  filtered.forEach(u => cnt[u.appid] = (cnt[u.appid] || 0) + 1);
  return Object.entries(cnt).sort((a, b) => b[1] - a[1]).slice(0, n).map(([a]) => a);
}
function steamRating(p){
  if(p >= 95) return '好评如潮';
  if(p >= 90) return '特别好评';
  if(p >= 80) return '好评';
  if(p >= 70) return '多半好评';
  if(p >= 40) return '褒贬不一';
  if(p >= 20) return '多半差评';
  return '差评';
}

/* ---------------- 主入口 ---------------- */
function renderDashboard(){
  renderKpis();
  renderDaily();
  renderPraise();
  renderSentiment();
  renderTopic();
}

/* ---------------- KPI（总量 / 推荐率） ---------------- */
function renderKpis(){
  const total = filtered.length;
  const rec = filtered.filter(v => v.rating === 1).length;
  const gamesN = new Set(filtered.map(v => v.appid)).size;
  const tl = $('[data-kpi="total"] .kpi-label');
  if(tl) tl.textContent = state.granularity === 'voice' ? '原声总量' : '观点总量';
  const tv = $('[data-kpi="total"] .kpi-value');
  const ts = $('[data-kpi="total"] .kpi-sub');
  const rv = $('[data-kpi="rec"] .kpi-value');
  const rs = $('[data-kpi="rec"] .kpi-sub');
  tv.textContent = fmtNum(total);
  ts.textContent = `覆盖 ${fmtNum(gamesN)} 款游戏 · ${grainName()}`;
  rv.textContent = pct(rec, total);
  rs.textContent = `${fmtNum(rec)} / ${fmtNum(total)}`;
}

/* 下钻表：总量 top5 游戏 */
function drillTotal(el){
  const cnt = {};
  filtered.forEach(u => cnt[u.appid] = (cnt[u.appid] || 0) + 1);
  const rows = Object.entries(cnt).sort((a, b) => b[1] - a[1]).slice(0, 5);
  let html = '<header>总量 TOP5 游戏</header>';
  rows.forEach(([appid, n]) => { html += `<div class="drill-row"><span class="dg">${esc(gameName(appid))}</span><b>${fmtNum(n)}</b></div>`; });
  el.innerHTML = html;
}
/* 下钻表：推荐率 top5 游戏 + steam 评级 */
function drillRec(el){
  const cnt = {};
  filtered.forEach(u => {
    if(!cnt[u.appid]) cnt[u.appid] = {n:0, rec:0};
    cnt[u.appid].n++;
    if(u.rating === 1) cnt[u.appid].rec++;
  });
  const rows = Object.entries(cnt).sort((a, b) => b[1].n - a[1].n).slice(0, 5);
  let html = '<header>推荐率 TOP5 游戏（按总量降序）</header>';
  rows.forEach(([appid, c]) => {
    const r = c.n ? c.rec / c.n * 100 : 0;
    html += `<div class="drill-row"><span class="dg">${esc(gameName(appid))}</span><span class="rate">${r.toFixed(1)}% · ${steamRating(r)}</span></div>`;
  });
  el.innerHTML = html;
}
function bindDrill(){
  const pop = document.createElement('div');
  pop.className = 'drill-pop';
  pop.id = 'drillPop';
  document.body.appendChild(pop);
  [['total', drillTotal], ['rec', drillRec]].forEach(([k, fn]) => {
    const kpi = $(`[data-kpi="${k}"]`);
    kpi.addEventListener('mouseenter', e => {
      fn(pop);
      const r = kpi.getBoundingClientRect();
      pop.style.left = Math.min(r.left, window.innerWidth - 380) + 'px';
      pop.style.top = (r.bottom + 8) + 'px';
      pop.classList.add('show');
    });
    kpi.addEventListener('mouseleave', () => pop.classList.remove('show'));
  });
}

/* ---------------- 折线图通用 ---------------- */
function weekStart(dateStr){
  const d = new Date(dateStr + 'T00:00:00');
  const day = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - day);
  return toDateStr(d);
}
function timeSeries(mode, byGame){
  const buckets = new Map();
  const keyOf = u => mode === 'week' ? weekStart(fmtDate(u.posted_at)) : fmtDate(u.posted_at);
  filtered.forEach(u => {
    const k = keyOf(u);
    if(!buckets.has(k)) buckets.set(k, {total:0, rec:0, games:{}});
    const b = buckets.get(k);
    b.total++;
    if(u.rating === 1) b.rec++;
    if(!b.games[u.appid]) b.games[u.appid] = {total:0, rec:0};
    b.games[u.appid].total++;
    if(u.rating === 1) b.games[u.appid].rec++;
  });
  const labels = [...buckets.keys()].sort();
  return {buckets, labels, keyOf};
}
function renderLine(container, labels, datasets, opts){
  // datasets: [{name, color, values}]
  const W = 760, H = 200, pl = 40, pr = 12, pt = 12, pb = 24;
  const iw = W - pl - pr, ih = H - pt - pb;
  const max = opts.max != null ? opts.max : Math.max(1, ...datasets.flatMap(d => d.values));
  const min = opts.min != null ? opts.min : 0;
  const n = labels.length;
  if(!n){ container.innerHTML = dEmpty(); return; }
  const x = i => n === 1 ? pl + iw / 2 : pl + (i / (n - 1)) * iw;
  const y = v => pt + ih - ((v - min) / (max - min || 1)) * ih;
  let grid = '';
  for(let g = 0; g <= 4; g++){
    const v = min + (max - min) * g / 4;
    const gy = y(v);
    grid += `<line class="lc-grid" x1="${pl}" y1="${gy}" x2="${W - pr}" y2="${gy}"/>`;
    grid += `<text class="lc-axis" x="${pl - 6}" y="${gy + 3}" text-anchor="end">${opts.fmt ? opts.fmt(v) : Math.round(v)}</text>`;
  }
  let lines = '';
  datasets.forEach((d, di) => {
    const pts = d.values.map((v, i) => `${x(i)},${y(v)}`).join(' ');
    lines += `<path class="lc-line" stroke="${d.color}" d="M${pts}"/>`;
    lines += d.values.map((v, i) => `<circle class="lc-dot" fill="${d.color}" cx="${x(i)}" cy="${y(v)}" r="3"/>`).join('');
  });
  let xlabels = '';
  const step = Math.max(1, Math.ceil(n / 8));
  labels.forEach((lb, i) => { if(i % step === 0) xlabels += `<text class="lc-axis" x="${x(i)}" y="${H - 8}" text-anchor="middle">${lb.slice(5)}</text>`; });
  container.innerHTML = `<svg class="line-chart" viewBox="0 0 ${W} ${H}">${grid}${lines}${xlabels}</svg>`;
  return {x, y, datasets, labels, W, H, max, min, pl, iw};
}
function bindLineHover(container){
  const tip = document.createElement('div');
  tip.className = 'lc-tooltip';
  tip.style.position = 'fixed';
  document.body.appendChild(tip);
  container.addEventListener('mousemove', e => {
    const svg = container.querySelector('svg');
    if(!svg) return;
    const data = container.__lc;
    if(!data) return;
    const rect = svg.getBoundingClientRect();
    const cx = (e.clientX - rect.left) / rect.width * data.W;
    const i = Math.round((cx - data.pl) / data.iw * (data.labels.length - 1));
    const idx = Math.max(0, Math.min(data.labels.length - 1, i));
    let html = `<b>${data.labels[idx]}</b>`;
    data.datasets.forEach(d => { html += `<br>${d.name}: <b>${data.fmtVal ? data.fmtVal(d.values[idx]) : d.values[idx]}</b>`; });
    tip.innerHTML = html;
    tip.style.display = 'block';
    tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 220) + 'px';
    tip.style.top = (e.clientY - 24) + 'px';
    tip.classList.add('show');
  });
  container.addEventListener('mouseleave', () => tip.classList.remove('show'));
}

/* ---------------- 每日趋势 ---------------- */
function renderDaily(){
  const c = chartState.daily;
  const el = $('#dailyBody');
  const {buckets, labels} = timeSeries(c.mode, c.byGame);
  if(!labels.length){ el.innerHTML = dEmpty(); return; }
  const datasets = [];
  if(!c.byGame){
    datasets.push({name: '总量', color: SERIES_COLORS[0], values: labels.map(lb => buckets.get(lb).total)});
  } else {
    topGames(5).forEach((appid, i) => {
      datasets.push({name: gameName(appid), color: SERIES_COLORS[i % 5], values: labels.map(lb => buckets.get(lb).games[appid] ? buckets.get(lb).games[appid].total : 0)});
    });
  }
  const data = renderLine(el, labels, datasets, {});
  el.__lc = data;
  el.__lc.fmtVal = v => v;
  // 图例
  if(c.byGame){
    let lg = '<div class="legend">';
    datasets.forEach(d => { lg += `<span class="l-item"><i style="background:${d.color}"></i>${esc(d.name)}</span>`; });
    lg += '</div>';
    el.insertAdjacentHTML('beforeend', lg);
  }
}
/* ---------------- 好评率趋势 ---------------- */
function renderPraise(){
  const c = chartState.praise;
  const el = $('#praiseBody');
  const {buckets, labels} = timeSeries(c.mode, c.byGame);
  if(!labels.length){ el.innerHTML = dEmpty(); return; }
  const datasets = [];
  if(!c.byGame){
    datasets.push({name: '整体好评率', color: SERIES_COLORS[0], values: labels.map(lb => { const b = buckets.get(lb); return b.total ? b.rec / b.total * 100 : 0; })});
  } else {
    topGames(5).forEach((appid, i) => {
      datasets.push({name: gameName(appid), color: SERIES_COLORS[i % 5], values: labels.map(lb => { const g = buckets.get(lb).games[appid]; return g && g.total ? g.rec / g.total * 100 : 0; })});
    });
  }
  const data = renderLine(el, labels, datasets, {max:100, min:0, fmt:v => v + '%'});
  el.__lc = data;
  el.__lc.fmtVal = v => v.toFixed(1) + '%';
  if(c.byGame){
    let lg = '<div class="legend">';
    datasets.forEach(d => { lg += `<span class="l-item"><i style="background:${d.color}"></i>${esc(d.name)}</span>`; });
    lg += '</div>';
    el.insertAdjacentHTML('beforeend', lg);
  }
}

/* ---------------- 情感分布（堆叠占比条） ---------------- */
function renderSentiment(){
  const c = chartState.sentiment;
  const el = $('#sentiBody');
  if(!filtered.length){ el.innerHTML = dEmpty(); return; }
  const calc = list => {
    const pos = list.filter(u => u.sentiment === 'positive').length;
    const neg = list.filter(u => u.sentiment === 'negative').length;
    const neu = list.length - pos - neg;
    return {pos, neu, neg, total: list.length};
  };
  let rows = [];
  if(!c.byGame){
    rows.push({name: '整体', ...calc(filtered)});
  } else {
    rows = topGames(5).map(appid => ({name: gameName(appid), ...calc(filtered.filter(u => u.appid === appid))}));
    rows.unshift({name: '整体', ...calc(filtered)});
  }
  const s = x => x.total ? (x / x.total * 100) : 0;
  let html = '<div class="stack-legend"><span><i style="background:var(--green)"></i>正向</span><span><i style="background:var(--yellow)"></i>中性</span><span><i style="background:var(--red)"></i>负向</span></div><div class="stack-list">';
  rows.forEach(r => {
    html += `<div class="stack-row">
      <span class="stack-name">${esc(r.name)}</span>
      <div class="stack-track" title="正向 ${r.pos} / 中性 ${r.neu} / 负向 ${r.neg}">
        <div class="pos" style="width:${s(r.pos)}%"></div>
        <div class="neu" style="width:${s(r.neu)}%"></div>
        <div class="neg" style="width:${s(r.neg)}%"></div>
      </div>
      <span class="stack-total">${fmtNum(r.total)}</span>
    </div>`;
  });
  html += '</div>';
  el.innerHTML = html;
}

/* ---------------- 主题分布（柱形 + 下钻） ---------------- */
function topicsOf(u, level, drill){
  if(state.granularity === 'voice'){
    if(level === 'L1') return u.topic ? [u.topic] : [];
    if(level === 'L2'){
      if(drill.length === 0 || u.topic === drill[0]) return u.sub_topics || [];
      return [];
    }
    if(drill.length >= 2 && u.topic === drill[0] && (u.sub_topics || []).includes(drill[1]))
      return (u.opinions || []).map(op => op.path.split('/').pop());
    return [];
  } else {
    const seg = u.path.split('/');
    if(level === 'L1') return [seg[0]];
    if(level === 'L2'){ if(drill.length === 0 || seg[0] === drill[0]) return seg[1] ? [seg[1]] : []; return []; }
    if(drill.length >= 2 && seg[0] === drill[0] && seg[1] === drill[1]) return [u.path];
    return [];
  }
}
function renderTopic(){
  const c = chartState.topic;
  const el = $('#topicBody');
  if(!filtered.length){ el.innerHTML = dEmpty(); return; }
  const drill = c.drill;
  let bread = '';
  if(drill.length){
    bread = '<div class="topic-breadcrumb">';
    if(drill.length >= 1) bread += `<button data-drillback="0">${esc(drill[0])}</button>`;
    if(drill.length >= 2) bread += ` / <button data-drillback="1">${esc(drill[1])}</button>`;
    bread += '</div>';
  }
  if(c.byGame){ renderTopicByGame(el, bread); return; }
  const cnt = {};
  filtered.forEach(u => topicsOf(u, c.level, drill).forEach(t => cnt[t] = (cnt[t] || 0) + 1));
  const rows = Object.entries(cnt).filter(([k]) => k).sort((a, b) => b[1] - a[1]).slice(0, 5);
  if(!rows.length){ el.innerHTML = bread + dEmpty('该粒度下无主题数据'); return; }
  const max = rows[0][1];
  let cols = '';
  rows.forEach(([name, n]) => {
    const h = Math.round(n / max * 100);
    cols += `<div class="bar-col">
      <div class="bar-val">${fmtNum(n)}</div>
      <div class="bar-box"><div class="bar-seg" style="height:${h}%"></div></div>
      <div class="bar-label" data-topic-name="${esc(name)}">${esc(name)}</div>
    </div>`;
  });
  el.innerHTML = bread + `<div class="bar-chart">${cols}</div>`;
  bindTopicInteractions(el, c, drill);
}
function renderTopicByGame(el, bread){
  const c = chartState.topic;
  const apps = topGames(5);
  const topicTotal = {};
  const topicByGame = {};
  filtered.forEach(u => {
    topicsOf(u, c.level, c.drill).forEach(t => {
      topicTotal[t] = (topicTotal[t] || 0) + 1;
      (topicByGame[t] = topicByGame[t] || {})[u.appid] = (topicByGame[t][u.appid] || 0) + 1;
    });
  });
  const topTopics = Object.entries(topicTotal).filter(([k]) => k).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([k]) => k);
  if(!topTopics.length){ el.innerHTML = bread + dEmpty('该粒度下无主题数据'); return; }
  const max = Math.max(1, ...topTopics.flatMap(t => apps.map(a => topicByGame[t][a] || 0)));
  let cols = '';
  topTopics.forEach(t => {
    cols += '<div class="bar-group"><div class="bar-group-bars">';
    apps.forEach((appid, i) => {
      const n = topicByGame[t][appid] || 0;
      const h = Math.round(n / max * 100);
      cols += `<div class="bar-box"><div class="bar-seg" style="height:${h}%;background:${SERIES_COLORS[i % 5]}" title="${esc(gameName(appid))}: ${n}"></div></div>`;
    });
    cols += '</div><div class="bar-label">' + esc(t) + '</div></div>';
  });
  let lg = '<div class="legend">';
  apps.forEach((appid, i) => { lg += `<span class="l-item"><i style="background:${SERIES_COLORS[i % 5]}"></i>${esc(gameName(appid))}</span>`; });
  lg += '</div>';
  el.innerHTML = bread + `<div class="bar-chart">${cols}</div>` + lg;
}
function bindTopicInteractions(el, c, drill){
  el.querySelectorAll('[data-topic-name]').forEach(lb => {
    lb.addEventListener('click', () => {
      const name = lb.dataset.topicName;
      if(c.level === 'L1'){ c.drill = [name]; c.level = 'L2'; }
      else if(c.level === 'L2'){ c.drill = [drill[0], name]; c.level = 'L3'; }
      syncTopicButtons();
      renderTopic();
    });
  });
  el.querySelectorAll('[data-drillback]').forEach(b => {
    b.addEventListener('click', () => {
      const back = +b.dataset.drillback;
      c.drill = c.drill.slice(0, back + 1);
      c.level = back === 0 ? 'L2' : 'L3';
      syncTopicButtons();
      renderTopic();
    });
  });
}

/* ---------------- 图表工具按钮 ---------------- */
function syncDailyButtons(){
  $$('[data-daily]').forEach(b => {
    const k = b.dataset.daily;
    const on = k === 'game' ? chartState.daily.byGame : chartState.daily.mode === k;
    b.classList.toggle('on', on);
  });
}
function syncPraiseButtons(){
  $$('[data-praise]').forEach(b => {
    const k = b.dataset.praise;
    const on = k === 'game' ? chartState.praise.byGame : chartState.praise.mode === k;
    b.classList.toggle('on', on);
  });
}
function syncSentiButtons(){
  $$('[data-senti]').forEach(b => b.classList.toggle('on', chartState.sentiment.byGame));
}
function syncTopicButtons(){
  $$('[data-topic]').forEach(b => {
    const k = b.dataset.topic;
    const on = k === 'game' ? chartState.topic.byGame : chartState.topic.level === k;
    b.classList.toggle('on', on);
  });
}
function bindChartTools(){
  $$('[data-daily]').forEach(b => b.addEventListener('click', () => {
    const k = b.dataset.daily;
    if(k === 'game') chartState.daily.byGame = !chartState.daily.byGame;
    else chartState.daily.mode = k;
    syncDailyButtons(); renderDaily();
  }));
  $$('[data-praise]').forEach(b => b.addEventListener('click', () => {
    const k = b.dataset.praise;
    if(k === 'game') chartState.praise.byGame = !chartState.praise.byGame;
    else chartState.praise.mode = k;
    syncPraiseButtons(); renderPraise();
  }));
  $$('[data-senti]').forEach(b => b.addEventListener('click', () => {
    chartState.sentiment.byGame = !chartState.sentiment.byGame;
    syncSentiButtons(); renderSentiment();
  }));
  $$('[data-topic]').forEach(b => b.addEventListener('click', () => {
    const k = b.dataset.topic;
    if(k === 'game') chartState.topic.byGame = !chartState.topic.byGame;
    else { chartState.topic.level = k; chartState.topic.drill = []; }
    syncTopicButtons(); renderTopic();
  }));
}

/* 折线悬停绑定（一次性） */
function initLineHover(){
  ['dailyBody', 'praiseBody'].forEach(id => {
    bindLineHover(document.getElementById(id));
  });
}

bindDrill();
bindChartTools();
initLineHover();
renderAll();
