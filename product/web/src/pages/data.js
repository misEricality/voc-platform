/* 系统管理 - 数据管理（原「时间序列」页，2026-09-05 移入系统管理子模块）
   P8 时间序列：按日评论量总量折线（2026-09-05 去情感堆叠）+ 每日明细
   （总量/正/中/负/已分析/兜底占比）；目标下拉 = monitored 白名单
   （6 款单机 + fetched B 站视频），无需登录（公开只读端点） */
Routes.data = async function (app) {
  const [steam, bili] = await Promise.all([
    // data 页运维豁免（2026-09-08）：include_hidden 拉全量（含 admin 隐藏目标）
    API.get('/api/targets?platform=steam&monitored=true&include_hidden=true'),
    API.get('/api/bilibili/videos?include_hidden=true'),
  ]);
  // 下拉顺序与其他页对齐（2026-09-06）：Steam 按发行日期倒序（/api/games/meta，失败回落名称序，
  // 同 dashboard/compare 序）；B站按投稿日期倒序（同 admin 列表序）
  const orderedSteam = steam.slice();
  try {
    const d = await API.get(`/api/games/meta?targets=${encodeURIComponent(steam.map(t => t.target_id).join(','))}`);
    const rd = {};
    d.items.forEach(m => { rd[m.target_id] = m.release_date || ''; });
    orderedSteam.sort((a, b) =>
      (rd[b.target_id] || '').localeCompare(rd[a.target_id] || '') ||
      a.name.localeCompare(b.name));
  } catch (e) {
    orderedSteam.sort((a, b) => a.name.localeCompare(b.name));
  }
  const orderedBili = bili.slice().sort((a, b) => {
    const pa = a.pubdate ? new Date(a.pubdate) : 0;
    const pb = b.pubdate ? new Date(b.pubdate) : 0;
    return pb - pa;
  });
  app.innerHTML = `
    <div class="page-head">
      <h1>系统管理 - 数据管理</h1>
      <span class="sub">每日评论量</span>
    </div>
    <div class="toolbar">
      <select id="selTarget">
        <option value="">全部目标汇总</option>
        ${orderedSteam.length ? `<optgroup label="Steam">${orderedSteam.map(t =>
          `<option value="${esc(t.target_id)}">${esc(t.name)}</option>`).join('')}</optgroup>` : ''}
        ${orderedBili.length ? `<optgroup label="B站">${orderedBili.map(v =>
          `<option value="${esc(v.target_id)}">${esc(v.title || v.bv_id)}</option>`).join('')}</optgroup>` : ''}
      </select>
      <select id="selDays">
        <option value="14">近 14 天</option>
        <option value="30" selected>近 30 天</option>
        <option value="90">近 90 天</option>
      </select>
    </div>
    <div class="card tall"><h3>每日评论量</h3><div class="chart" style="height:420px" id="chTrend"></div></div>
    <div class="card section-gap"><h3>每日明细</h3>
      <div style="overflow:auto;max-height:320px"><table class="tbl fixed" id="tblDays"></table></div>
    </div>`;

  async function render() {
    const params = new URLSearchParams({
      target: document.getElementById('selTarget').value,
      days: document.getElementById('selDays').value,
    });
    for (const [k, v] of [...params.entries()]) if (!v) params.delete(k);
    const d = await API.get(`/api/trends?${params}`);
    const p = Charts.palette();
    const days = d.items.map(i => i.day);

    Charts.render('chTrend', {
      xAxis: { type: 'category', data: days, axisLabel: { color: p.muted } },
      yAxis: { type: 'value', axisLabel: { color: p.muted }, splitLine: { lineStyle: { color: Charts.token('--line2') } } },
      series: [
        { name: '总量', type: 'line', smooth: true, symbol: 'none', lineStyle: { color: p.primary, width: 2.5 }, data: d.items.map(i => i.total) },
      ],
      tooltip: { trigger: 'axis' },
    });

    document.getElementById('tblDays').innerHTML = `
      <thead><tr><th style="width:16%">日期</th><th class="num">总量</th><th class="num">正向</th><th class="num">中性</th><th class="num">负向</th><th class="num">已分析</th><th class="num">兜底占比</th></tr></thead>
      <tbody>${d.items.slice().reverse().map(i => `<tr>
        <td>${esc(i.day)}</td><td class="num">${fmtNum(i.total)}</td>
        <td class="num" style="color:var(--pos)">${fmtNum(i.positive)}</td>
        <td class="num" style="color:var(--neu)">${fmtNum(i.neutral)}</td>
        <td class="num" style="color:var(--neg)">${fmtNum(i.negative)}</td>
        <td class="num">${fmtNum(i.analyzed)}</td>
        <td class="num">${i.fallback_pct ?? 0}%</td></tr>`).join('') || '<tr><td colspan="7" class="empty">时间窗内无数据</td></tr>'}</tbody>`;
  }

  document.getElementById('selTarget').addEventListener('change', render);
  document.getElementById('selDays').addEventListener('change', render);
  await render();

  /* ---- 跨页上下文（2026-09-09 阶段 8 落地）：供 AI 抽屉「引用当前查询」按钮使用 ---- */
  function setAgentContext() {
    const tid = document.getElementById('selTarget').value;
    const days = document.getElementById('selDays').value;
    const name = tid
      ? (orderedSteam.find(t => t.target_id === tid)?.name || orderedBili.find(v => v.target_id === tid)?.title || tid)
      : '全部目标';
    const params = {};
    if (tid) params.target = tid;
    if (days) params.days = days;
    window.__pageAgentContext = {
      page: 'data',
      target_id: tid,
      target_name: name,
      days: days,
      label: `${name} · 近 ${days} 天`,
      quick_query: `/api/trends?${new URLSearchParams(params)}`,
    };
  }
  document.getElementById('selTarget').addEventListener('change', setAgentContext);
  document.getElementById('selDays').addEventListener('change', setAgentContext);
  setAgentContext();  // 初始 baseline
};
