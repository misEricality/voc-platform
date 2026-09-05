/* 系统管理 - 数据管理（原「时间序列」页，2026-09-05 移入系统管理子模块）
   P8 时间序列：按日评论量总量折线（2026-09-05 去情感堆叠）+ 每日明细
   （总量/正/中/负/已分析/兜底占比）；目标下拉 = monitored 白名单
   （6 款单机 + fetched B 站视频），无需登录（公开只读端点） */
Routes.data = async function (app) {
  const [steam, bili] = await Promise.all([
    API.get('/api/targets?platform=steam&monitored=true'),
    API.get('/api/bilibili/videos'),
  ]);
  app.innerHTML = `
    <div class="page-head">
      <h1>系统管理 - 数据管理</h1>
      <span class="sub">每日评论量</span>
    </div>
    <div class="toolbar">
      <select id="selTarget">
        <option value="">全部目标汇总</option>
        ${steam.length ? `<optgroup label="Steam">${steam.map(t =>
          `<option value="${esc(t.target_id)}">${esc(t.name)}</option>`).join('')}</optgroup>` : ''}
        ${bili.length ? `<optgroup label="B站">${bili.map(v =>
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
};
