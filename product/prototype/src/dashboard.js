/* ============================================================
   数据看板 v3 —— 图表渲染（共享全局）
   颗粒度感知 + 悬停高亮元素并固定显示标签 + 推荐率卡片
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

/* ---------------- KPI（总量 / 好评率 / 推荐率） ---------------- */
function renderKpis(){
  const gran = state.granularity;
  // 总量：原声/观点
  const total = filtered.length;
  const gamesN = new Set(filtered.map(v => v.appid)).size;
  const tl = $('[data-kpi="total"] .kpi-label');
  if(tl) tl.textContent = gran === 'voice' ? '原声总量' : '观点总量';
  $('[data-kpi="total"] .kpi-value').textContent = fmtNum(total);
  $('[data-kpi="total"] .kpi-sub').textContent = `覆盖 ${fmtNum(gamesN)} 款游戏 · ${grainName()}`;

  // 好评率：= 正向情感 / 总量（与颗粒度联动：原声用整体情感、观点用观点情感）；只显示百分数
  const pos = filtered.filter(v => v.sentiment === 'positive').length;
  const pk = $('[data-kpi="praise"]');
  if(pk){
    pk.querySelector('.kpi-label').textContent = gran === 'voice' ? '原声好评率' : '观点好评率';
    pk.querySelector('.kpi-value').textContent = pct(pos, total);
    pk.querySelector('.kpi-sub').textContent = `正向 ${fmtNum(pos)} / 总量 ${fmtNum(total)}`;
  }
}

/* 下钻表：副卡 */
function drillTotal(el){
  const cnt = {};
  filtered.forEach(u => cnt[u.appid] = (cnt[u.appid] || 0) + 1);
  const rows = Object.entries(cnt).sort((a, b) => b[1] - a[1]).slice(0, 5);
  let html = '<header>TOP5 游戏（按总量降序）</header>';
  rows.forEach(([appid, n]) => { html += `<div class="drill-row"><span class="dg">${esc(gameName(appid))}</span><b>${fmtNum(n)}</b></div>`; });
  el.innerHTML = html;
}
function drillPraise(el){
  const cnt = {};
  filtered.forEach(u => {
    if(!cnt[u.appid]) cnt[u.appid] = {n:0, pos:0};
    cnt[u.appid].n++;
    if(u.sentiment === 'positive') cnt[u.appid].pos++;
  });
  const rows = Object.entries(cnt).sort((a, b) => b[1].n - a[1].n).slice(0, 5);
  let html = '<header>TOP5 游戏（按总量降序）</header>';
  rows.forEach(([appid, c]) => {
    const r = c.n ? c.pos / c.n * 100 : 0;
    html += `<div class="drill-row"><span class="dg">${esc(gameName(appid))}</span><span class="rate">${r.toFixed(1)}%</span></div>`;
  });
  el.innerHTML = html;
}
function bindDrill(){
  const pop = document.createElement('div');
  pop.className = 'drill-pop';
  pop.id = 'drillPop';
  document.body.appendChild(pop);
  [['total', drillTotal], ['praise', drillPraise]].forEach(([k, fn]) => {
    const kpi = $(`[data-kpi="${k}"]`);
    kpi.addEventListener('mouseenter', () => {
      fn(pop);
      const r = kpi.getBoundingClientRect();
      pop.style.left = Math.min(r.left, window.innerWidth - 380) + 'px';
      pop.style.top = (r.bottom + 8) + 'px';
      pop.classList.add('show');
    });
    kpi.addEventListener('mouseleave', () => pop.classList.remove('show'));
  });
}

/* ---------------- 折线图（hover 高亮元素 + 固定标签） ---------------- */
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
    if(!buckets.has(k)) buckets.set(k, {total:0, pos:0, rec:0, games:{}});
    const b = buckets.get(k);
    b.total++;
    if(u.sentiment === 'positive') b.pos++;
    if(u.rating === 1) b.rec++;
    if(!b.games[u.appid]) b.games[u.appid] = {total:0, pos:0, rec:0};
    b.games[u.appid].total++;
    if(u.sentiment === 'positive') b.games[u.appid].pos++;
    if(u.rating === 1) b.games[u.appid].rec++;
  });
  const labels = [...buckets.keys()].sort();
  return {buckets, labels, keyOf};
}
/* nice ticks：自适应整十/整百/整千刻度（5 等分），保证纵轴整洁 */
function niceTicks(maxV, n = 5){
  if(maxV <= 0) return {step: 1, niceMax: 1};
  const rough = maxV / n;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  let step;
  if(norm < 1.5) step = 1 * mag;
  else if(norm < 3) step = 2 * mag;
  else if(norm < 7) step = 5 * mag;
  else step = 10 * mag;
  const niceMax = Math.ceil(maxV / step) * step;
  return {step, niceMax};
}
function fmtTick(v, step){
  if(step >= 1000) return (v/1000).toFixed(step%1000===0?0:1).replace(/\.0$/,'') + 'k';
  if(step >= 1) return Math.round(v).toString();
  return v.toFixed(2);
}
function renderLine(container, labels, datasets, opts){
  const W = 760, H = 200, pl = 44, pr = 12, pt = 12, pb = 24;
  const iw = W - pl - pr, ih = H - pt - pb;
  const rawMax = opts.max != null ? opts.max : Math.max(1, ...datasets.flatMap(d => d.values));
  const min = opts.min != null ? opts.min : 0;
  // 纵轴 nice 化（好评率固定 0-100 不参与）
  const nt = opts.max != null ? {step: (opts.max - opts.min) / 4, niceMax: opts.max} : niceTicks(rawMax, 5);
  const max = nt.niceMax;
  const ntstep = nt.step;
  const n = labels.length;
  if(!n){ container.innerHTML = dEmpty(); return; }
  const x = i => n === 1 ? pl + iw / 2 : pl + (i / (n - 1)) * iw;
  const y = v => pt + ih - ((v - min) / (max - min || 1)) * ih;
  let grid = '';
  // 5 段刻度（含顶/底）
  for(let g = 0; g <= 4; g++){
    const v = min + (max - min) * g / 4;
    const gy = y(v);
    grid += `<line class="lc-grid" x1="${pl}" y1="${gy}" x2="${W - pr}" y2="${gy}"/>`;
    grid += `<text class="lc-axis" x="${pl - 6}" y="${gy + 3}" text-anchor="end">${opts.fmt ? opts.fmt(v) : fmtTick(v, ntstep)}</text>`;
  }
  // 每个 dataset 一个 group，方便 hover 高亮整条
  let lines = '';
  datasets.forEach((d, di) => {
    const pts = d.values.map((v, i) => `${x(i)},${y(v)}`).join(' ');
    lines += `<g class="lc-set" data-di="${di}">
      <path class="lc-line" stroke="${d.color}" d="M${pts}"/>
      ${d.values.map((v, i) => `<circle class="lc-dot" fill="${d.color}" cx="${x(i)}" cy="${y(v)}" r="3"/>`).join('')}
    </g>`;
  });
  // 标签层（hover 某 set 时显示该 set 的所有标签）
  let labelsSvg = '<g class="lc-labels">';
  datasets.forEach((d, di) => {
    d.values.forEach((v, i) => {
      const valTxt = opts.fmtVal ? opts.fmtVal(v) : Math.round(v);
      labelsSvg += `<text class="lc-label" data-di="${di}" x="${x(i)}" y="${y(v) - 8}" text-anchor="middle" fill="${d.color}" style="display:none">${valTxt}</text>`;
    });
  });
  labelsSvg += '</g>';
  let xlabels = '';
  const step = Math.max(1, Math.ceil(n / 8));
  labels.forEach((lb, i) => { if(i % step === 0) xlabels += `<text class="lc-axis" x="${x(i)}" y="${H - 8}" text-anchor="middle">${lb.slice(5)}</text>`; });
  container.innerHTML = `<svg class="line-chart" viewBox="0 0 ${W} ${H}">${grid}${lines}${labelsSvg}${xlabels}</svg>`;

  // hover 绑定
  const svg = container.querySelector('svg');
  container.querySelectorAll('.lc-set').forEach(g => {
    g.addEventListener('mouseenter', () => {
      const di = g.dataset.di;
      // 暗化其它 set
      svg.querySelectorAll('.lc-set').forEach(gg => gg.classList.toggle('dim', gg.dataset.di !== di));
      svg.querySelectorAll('.lc-label').forEach(lb => lb.style.display = lb.dataset.di === di ? '' : 'none');
    });
    g.addEventListener('mouseleave', () => {
      svg.querySelectorAll('.lc-set').forEach(gg => gg.classList.remove('dim'));
      svg.querySelectorAll('.lc-label').forEach(lb => lb.style.display = 'none');
    });
  });
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
  renderLine(el, labels, datasets, {fmtVal: v => fmtNum(v)});
  if(c.byGame){
    let lg = '<div class="legend">';
    datasets.forEach(d => { lg += `<span class="l-item"><i style="background:${d.color}"></i>${esc(d.name)}</span>`; });
    lg += '</div>';
    el.insertAdjacentHTML('beforeend', lg);
  }
}

/* ---------------- 好评率趋势（用正向情感占比） ---------------- */
function renderPraise(){
  const c = chartState.praise;
  const el = $('#praiseBody');
  const {buckets, labels} = timeSeries(c.mode, c.byGame);
  if(!labels.length){ el.innerHTML = dEmpty(); return; }
  const datasets = [];
  if(!c.byGame){
    datasets.push({name: '整体好评率', color: SERIES_COLORS[0], values: labels.map(lb => { const b = buckets.get(lb); return b.total ? b.pos / b.total * 100 : 0; })});
  } else {
    topGames(5).forEach((appid, i) => {
      datasets.push({name: gameName(appid), color: SERIES_COLORS[i % 5], values: labels.map(lb => { const g = buckets.get(lb).games[appid]; return g && g.total ? g.pos / g.total * 100 : 0; })});
    });
  }
  renderLine(el, labels, datasets, {max:100, min:0, fmt:v => v + '%', fmtVal: v => v.toFixed(1) + '%'});
  if(c.byGame){
    let lg = '<div class="legend">';
    datasets.forEach(d => { lg += `<span class="l-item"><i style="background:${d.color}"></i>${esc(d.name)}</span>`; });
    lg += '</div>';
    el.insertAdjacentHTML('beforeend', lg);
  }
}

/* ---------------- 情感分布（堆叠占比条）hover 整行 ---------------- */
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
  const sPct = (n, t) => t ? (n / t * 100) : 0;
  // 图例移至左下角，与其他图表对齐
  let html = '<div class="stack-list">';
  rows.forEach((r, ri) => {
    html += `<div class="stack-row" data-ri="${ri}">
      <span class="stack-name" title="${esc(r.name)}">${esc(r.name)}</span>
      <div class="stack-track" title="正向 ${r.pos} / 中性 ${r.neu} / 负向 ${r.neg}">
        <div class="pos" style="width:${sPct(r.pos, r.total)}%"></div>
        <div class="neu" style="width:${sPct(r.neu, r.total)}%"></div>
        <div class="neg" style="width:${sPct(r.neg, r.total)}%"></div>
      </div>
      <span class="stack-total">${fmtNum(r.total)}</span>
      <div class="stack-label" style="display:none">正向 ${fmtNum(r.pos)} · 中性 ${fmtNum(r.neu)} · 负向 ${fmtNum(r.neg)}</div>
    </div>`;
  });
  html += '</div><div class="stack-legend"><span><i style="background:var(--green)"></i>正向</span><span><i style="background:var(--yellow)"></i>中性</span><span><i style="background:var(--red)"></i>负向</span></div>';
  el.innerHTML = html;
  el.querySelectorAll('.stack-row').forEach(row => {
    row.addEventListener('mouseenter', () => {
      row.querySelector('.stack-label').style.display = '';
    });
    row.addEventListener('mouseleave', () => {
      row.querySelector('.stack-label').style.display = 'none';
    });
  });
}

/* ---------------- 主题分布（柱形 + 下钻）---------------- */
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
  // 观点报表 L3：库里观点最深就到 L3；命中后展示完整 path 柱
  // 这里保持 L1/L2/L3 均可下钻，L3 即叶子
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
    cols += `<div class="bar-col" data-barname="${esc(name)}">
      <div class="bar-val" style="display:none">${fmtNum(n)}</div>
      <div class="bar-box"><div class="bar-seg" style="height:${h}%"></div></div>
      <div class="bar-label" data-topic-name="${esc(name)}">${esc(name)}</div>
    </div>`;
  });
  el.innerHTML = bread + `<div class="bar-chart topic-pad">${cols}</div>`;
  el.querySelectorAll('.bar-col').forEach(col => {
    col.addEventListener('mouseenter', () => {
      el.querySelectorAll('.bar-col').forEach(cc => cc.classList.toggle('dim', cc !== col));
      col.querySelector('.bar-val').style.display = '';
    });
    col.addEventListener('mouseleave', () => {
      el.querySelectorAll('.bar-col').forEach(cc => cc.classList.remove('dim'));
      col.querySelector('.bar-val').style.display = 'none';
    });
  });
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
      cols += `<div class="bar-box" data-n="${n}" data-appid="${appid}"><div class="bar-seg" style="height:${h}%;background:${SERIES_COLORS[i % 5]}"></div><div class="bar-val" style="display:none">${fmtNum(n)}</div></div>`;
    });
    cols += '</div><div class="bar-label">' + esc(t) + '</div></div>';
  });
  let lg = '<div class="legend">';
  apps.forEach((appid, i) => { lg += `<span class="l-item"><i style="background:${SERIES_COLORS[i % 5]}"></i>${esc(gameName(appid))}</span>`; });
  lg += '</div>';
  el.innerHTML = bread + `<div class="bar-chart topic-pad">${cols}</div>` + lg;
  // 按游戏 hover：图表中同 appid 的所有柱子同时响应 + bar-val 统一在柱子上方（CSS 绝对定位）
  el.querySelectorAll('.bar-box').forEach(b => {
    b.addEventListener('mouseenter', () => {
      const appid = b.dataset.appid;
      el.querySelectorAll('.bar-box').forEach(bb => {
        const sameApp = bb.dataset.appid === appid;
        bb.classList.toggle('dim', !sameApp);
        bb.querySelector('.bar-val').style.display = sameApp ? '' : 'none';
      });
    });
    b.addEventListener('mouseleave', () => {
      el.querySelectorAll('.bar-box').forEach(bb => {
        bb.classList.remove('dim');
        bb.querySelector('.bar-val').style.display = 'none';
      });
    });
  });
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

bindDrill();
bindChartTools();
renderAll();
