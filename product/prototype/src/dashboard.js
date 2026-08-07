/* ============================================================
   数据看板 v2 —— 渲染逻辑（由构建脚本拼接至 app.js 之后，
   共享 DATA / VOICES / filtered / state / 工具函数）
   ============================================================ */

function dEmpty(msg){
  return `<div class="dempty">${msg || '当前筛选下无数据'}</div>`;
}

function renderDashboard(){
  const total = filtered.length;
  const rec = filtered.filter(v => v.rating === 1).length;
  const pos = filtered.filter(v => v.sentiment === 'positive').length;
  const neg = filtered.filter(v => v.sentiment === 'negative').length;
  const gamesN = new Set(filtered.map(v => v.appid)).size;
  const plays = filtered.map(v => v.playtime_at_review).filter(x => x !== null && x !== undefined);
  const avgH = plays.length ? plays.reduce((a, b) => a + b, 0) / plays.length / 60 : null;

  const setKpi = (k, v, sub) => {
    const el = $(`[data-kpi="${k}"]`);
    if(el) el.textContent = v;
    const s = $(`[data-kpi="${k}Sub"]`);
    if(s) s.textContent = sub;
  };
  setKpi('total', fmtNum(total), `覆盖 ${fmtNum(gamesN)} 款游戏`);
  setKpi('rec', pct(rec, total), `${fmtNum(rec)} 条推荐评论`);
  setKpi('pos', pct(pos, total), `${fmtNum(pos)} 条`);
  setKpi('neg', pct(neg, total), `${fmtNum(neg)} 条`);
  setKpi('play', avgH === null ? '-' : avgH.toLocaleString('zh-CN', {maximumFractionDigits:1}) + ' 小时',
    `有游玩时长样本 ${fmtNum(plays.length)} 条`);

  drawTrend(total);
  drawRec(rec, total);
  drawSenti(pos, neg, total);
  drawL1();
  drawL2();
  drawGame();
  drawPlay(plays);
  drawOps();
}

/* ---- 每日原声量趋势（情感堆叠柱） ---- */
function drawTrend(total){
  const el = $('#dTrend');
  if(!total){ el.innerHTML = dEmpty(); return; }
  const byDate = {};
  filtered.forEach(v => {
    const d = fmtDate(v.posted_at) || '未知';
    if(!byDate[d]) byDate[d] = {pos:0, neu:0, neg:0, total:0};
    const b = byDate[d];
    b.total++;
    if(v.sentiment === 'positive') b.pos++;
    else if(v.sentiment === 'negative') b.neg++;
    else b.neu++;
  });
  const days = Object.keys(byDate).sort();
  const max = Math.max(1, ...days.map(d => byDate[d].total));
  const h = x => Math.max(2, Math.round(x / max * 100));
  el.innerHTML = '<div class="trend">' + days.map(d => {
    const b = byDate[d];
    return `<div class="tcol" title="${d} · 正向 ${b.pos} / 中性 ${b.neu} / 负向 ${b.neg}">
      <div class="tnum">${b.total}</div>
      <div class="bars">
        <div class="seg pos" style="height:${h(b.pos)}%"></div>
        <div class="seg neu" style="height:${h(b.neu)}%"></div>
        <div class="seg neg" style="height:${h(b.neg)}%"></div>
      </div>
      <div class="tlabel">${d.slice(5)}</div></div>`;
  }).join('') + '</div>';
}

/* ---- 推荐构成（环形） ---- */
function drawRec(rec, total){
  const el = $('#dRec');
  if(!total){ el.innerHTML = dEmpty(); return; }
  const recP = (rec / total * 100).toFixed(1);
  el.innerHTML = `<div class="donut-box">
    <div class="donut" style="background:conic-gradient(var(--green) 0 ${recP}%, var(--red) ${recP}% 100%)" data-center="${fmtNum(total)}\n评论总数"></div>
    <div class="donut-legend">
      <div><span class="dot" style="background:var(--green)"></span>推荐<b>${fmtNum(rec)} · ${pct(rec, total)}</b></div>
      <div><span class="dot" style="background:var(--red)"></span>不推荐<b>${fmtNum(total - rec)} · ${pct(total - rec, total)}</b></div>
    </div></div>`;
}

/* ---- 整体情感分布（三色条） ---- */
function drawSenti(pos, neg, total){
  const el = $('#dSenti');
  if(!total){ el.innerHTML = dEmpty(); return; }
  const neu = total - pos - neg;
  const s = x => Math.max(0, x / total * 100);
  el.innerHTML = `<div class="senti-bar">
    <div class="senti-track">
      <div class="pos" style="width:${s(pos)}%" title="正向">${pos ? '正向 ' + pct(pos, total) : ''}</div>
      <div class="neu" style="width:${s(neu)}%" title="中性">${neu ? '中性 ' + pct(neu, total) : ''}</div>
      <div class="neg" style="width:${s(neg)}%" title="负向">${neg ? '负向 ' + pct(neg, total) : ''}</div>
    </div>
    <div class="senti-legend"><span>正向 <b>${fmtNum(pos)} · ${pct(pos, total)}</b></span><span>中性 <b>${fmtNum(neu)} · ${pct(neu, total)}</b></span><span>负向 <b>${fmtNum(neg)} · ${pct(neg, total)}</b></span></div>
  </div>`;
}

/* ---- 主题 L1 排行 ---- */
function drawL1(){
  const el = $('#dL1');
  if(!filtered.length){ el.innerHTML = dEmpty(); return; }
  const cnt = {};
  filtered.forEach(v => { const k = v.topic || '未标注'; cnt[k] = (cnt[k] || 0) + 1; });
  const rows = Object.entries(cnt).sort((a, b) => b[1] - a[1]);
  const max = rows[0][1];
  el.innerHTML = '<div class="dbody-list">' + rows.map(([k, n]) =>
    `<div class="hbar-row"><span class="hname">${esc(k)}</span><div class="htrack"><div class="hfill" style="width:${(n / max * 100).toFixed(1)}%"></div></div><span class="hcount">${fmtNum(n)}</span><span class="hpct">${pct(n, filtered.length)}</span></div>`
  ).join('') + '</div>';
}

/* ---- 主题 L2 TOP10 ---- */
function drawL2(){
  const el = $('#dL2');
  const cnt = {};
  filtered.forEach(v => (v.sub_topics || []).forEach(s => cnt[s] = (cnt[s] || 0) + 1));
  const rows = Object.entries(cnt).sort((a, b) => b[1] - a[1]).slice(0, 10);
  if(!rows.length){ el.innerHTML = dEmpty('当前筛选下无 L2 标签数据'); return; }
  const max = rows[0][1];
  el.innerHTML = '<div class="dbody-list">' + rows.map(([k, n]) =>
    `<div class="hbar-row"><span class="hname">${esc(k)}</span><div class="htrack"><div class="hfill" style="width:${(n / max * 100).toFixed(1)}%"></div></div><span class="hcount">${fmtNum(n)}</span><span class="hpct">${pct(n, filtered.length)}</span></div>`
  ).join('') + '</div>';
}

/* ---- 各游戏原声构成（情感堆叠） ---- */
function drawGame(){
  const el = $('#dGame');
  const cnt = {};
  GAMES.forEach(g => cnt[g.appid] = {pos:0, neu:0, neg:0, total:0, name:g.name});
  filtered.forEach(v => {
    const c = cnt[v.appid] || (cnt[v.appid] = {pos:0, neu:0, neg:0, total:0, name:v.game});
    c.total++;
    if(v.sentiment === 'positive') c.pos++;
    else if(v.sentiment === 'negative') c.neg++;
    else c.neu++;
  });
  const rows = Object.values(cnt).filter(c => c.total > 0).sort((a, b) => b.total - a.total);
  if(!rows.length){ el.innerHTML = dEmpty(); return; }
  el.innerHTML = '<div class="g-legend"><span><i style="background:var(--green)"></i>正向</span><span><i style="background:#d9b54e"></i>中性</span><span><i style="background:var(--red)"></i>负向</span></div>' +
    rows.map(c =>
      `<div class="grow" title="${esc(c.name)} · 正向 ${c.pos} / 中性 ${c.neu} / 负向 ${c.neg}">
        <span class="gname">${esc(c.name)}</span>
        <div class="gtrack"><div class="pos" style="width:${(c.pos / c.total * 100).toFixed(1)}%"></div><div class="neu" style="width:${(c.neu / c.total * 100).toFixed(1)}%"></div><div class="neg" style="width:${(c.neg / c.total * 100).toFixed(1)}%"></div></div>
        <span class="gtotal">${fmtNum(c.total)}</span></div>`
    ).join('');
}

/* ---- 评论时游玩时长分布 ---- */
function drawPlay(plays){
  const el = $('#dPlay');
  if(!plays.length){ el.innerHTML = dEmpty('当前筛选下无游玩时长数据'); return; }
  const buckets = [
    {lo:0, hi:60, cls:'b1', label:'< 1 小时'},
    {lo:60, hi:600, cls:'b2', label:'1-10 小时'},
    {lo:600, hi:3600, cls:'b3', label:'10-100 小时'},
    {lo:3600, hi:Infinity, cls:'b4', label:'100 小时以上'}
  ];
  buckets.forEach(x => x.n = 0);
  plays.forEach(m => {
    const b = buckets.find(x => m >= x.lo && m < x.hi);
    if(b) b.n++;
  });
  const max = Math.max(1, ...buckets.map(x => x.n));
  el.innerHTML = `<div class="play-box">
    <div class="play-track">` +
    buckets.map(x => `<div class="${x.cls}" style="width:${(x.n / max * 100).toFixed(1)}%" title="${x.label}">${x.n ? x.n : ''}</div>`).join('') +
    `</div>
    <div class="play-legend">` +
    buckets.map(x => `<span>${x.label}<b> ${fmtNum(x.n)} · ${pct(x.n, plays.length)}</b></span>`).join('') +
    `</div></div>`;
}

/* ---- 标签级观点洞察（观点情感 + 路径 TOP10） ---- */
function drawOps(){
  const el = $('#dOps');
  const meta = $('#dOpMeta');
  const ops = [];
  filtered.forEach(v => (v.opinions || []).forEach(op => ops.push(op)));
  const covered = new Set(filtered.filter(v => v.opinions && v.opinions.length).map(v => v.id)).size;
  meta.textContent = `观点样本 ${fmtNum(ops.length)} 条 · 覆盖 ${fmtNum(covered)} 条评论`;
  if(!ops.length){
    el.innerHTML = '<div class="dempty" style="grid-column:1/-1">当前筛选下无标签级观点数据（L3 观点覆盖率约 9%，可放宽筛选范围查看）</div>';
    return;
  }
  const sCnt = {positive:0, neutral:0, negative:0};
  const pCnt = {};
  ops.forEach(op => {
    if(sCnt[op.sentiment] !== undefined) sCnt[op.sentiment]++;
    pCnt[op.path] = (pCnt[op.path] || 0) + 1;
  });
  const pos = sCnt.positive, neu = sCnt.neutral, neg = sCnt.negative;
  const s = x => Math.max(0, x / ops.length * 100);
  const left = `<div class="op-left">
    <div class="senti-track">
      <div class="pos" style="width:${s(pos)}%" title="正向">${pos ? '正向 ' + pct(pos, ops.length) : ''}</div>
      <div class="neu" style="width:${s(neu)}%" title="中性">${neu ? '中性 ' + pct(neu, ops.length) : ''}</div>
      <div class="neg" style="width:${s(neg)}%" title="负向">${neg ? '负向 ' + pct(neg, ops.length) : ''}</div>
    </div>
    <div class="senti-legend"><span>正向 <b>${fmtNum(pos)} · ${pct(pos, ops.length)}</b></span><span>中性 <b>${fmtNum(neu)} · ${pct(neu, ops.length)}</b></span><span>负向 <b>${fmtNum(neg)} · ${pct(neg, ops.length)}</b></span></div>
  </div>`;
  const rows = Object.entries(pCnt).sort((a, b) => b[1] - a[1]).slice(0, 10);
  const max = rows[0][1];
  const right = '<div class="op-right"><div class="dbody-list">' + rows.map(([k, n]) =>
    `<div class="hbar-row"><span class="hname">${esc(k)}</span><div class="htrack"><div class="hfill" style="width:${(n / max * 100).toFixed(1)}%"></div></div><span class="hcount">${fmtNum(n)}</span><span class="hpct">${pct(n, ops.length)}</span></div>`
  ).join('') + '</div></div>';
  el.innerHTML = left + right;
}
