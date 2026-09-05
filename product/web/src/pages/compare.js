/* 游戏对比看板（2026-09-04 · 线框图重设计）
   6 款单机（monitored 白名单）→ 封面卡片多选筛选（默认前 3，至少 2）
   → 情感对比（横向 100% 堆叠）+ 口碑对比（评论量 × 推荐率散点）
   → Top 主题对比（L2 观点粒度，每游戏一图，负向/正向切换，默认负向）
   → 指标对比表（评论量/推荐量/推荐率/观点好评率/发行天数/Steam 推荐评级）

   加载策略（2026-09-04 修复首屏 30s+）：/api/games/meta 为 stale-while-revalidate ——
   立即返回现有行，缺字段的后台线程刷新；前端 3s 轮询直至 refreshing 清空。
   元数据未到位时卡片显示占位（发行日排序在元数据到达后重排）。

   同期/累计：同期 = 各游戏取**等长的最近 N 天窗口**（N = 选中中最晚发行游戏的
   已发行天数，按库内最新评论日截止）。注：库内数据自 2026-07-31 起采集，「发行后
   前 N 天」的历史窗口与库内覆盖不重叠（对比页图表曾因此全空），2026-09-04 经
   口径调整改为等长最近窗口；累计 = 不做时间截断。 */
Routes.compare = async function (app) {
  const targets = await API.get('/api/targets?platform=steam&monitored=true');
  if (!targets.length) { app.innerHTML = '<div class="empty">库中暂无 Steam 游戏数据</div>'; return; }

  let metaMap = {};   // target_id → meta（异步加载）
  // 卡片序 = 发行日期倒序（缺发行日排最后，按名称稳定排序）；meta 到位前用默认序
  let games = targets.slice().sort((a, b) => a.name.localeCompare(b.name));
  const selected = new Set(games.slice(0, 3).map(g => g.target_id));  // 默认勾选前 3
  const state = { mode: '同期', polar: 'negative' };  // 同期 | 累计；negative | positive
  let reqSeq = 0;
  let metaTimer = null;

  const RATING_CLASS = {
    '好评如潮': 'r-best', '特别好评': 'r-great', '好评': 'r-good', '褒贬不一': 'r-mixed',
  };
  const ratingClass = desc => RATING_CLASS[desc] || 'r-bad';
  const appidOf = tid => String(tid).split(':')[1] || tid;
  const addDays = (ds, n) => {
    const d = new Date(ds + 'T00:00:00'); d.setDate(d.getDate() + n);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };
  const daysBetween = (a, b) => Math.floor((new Date(b + 'T00:00:00') - new Date(a + 'T00:00:00')) / 86400000);
  function daysSince(ds) { return daysBetween(ds, new Date().toISOString().slice(0, 10)); }

  // 封面兜底链：本地 /covers → Steam CDN 竖版 → 名称占位（全局函数供 onerror 内联调用）
  window.__gcImgError = function (img) {
    if (img.dataset.fb !== '1') {
      img.dataset.fb = '1';
      img.src = `https://cdn.cloudflare.steamstatic.com/steam/apps/${img.dataset.appid}/library_600x900.jpg`;
    } else {
      img.style.display = 'none';
      img.closest('.gc-cover').classList.add('noimg');
    }
  };

  app.innerHTML = `
    <div class="page-head">
      <h1>游戏对比看板</h1>
      <span class="head-actions">
        <div class="seg sm" id="segMode">
          <button data-mode="同期">同期</button>
          <button data-mode="累计">累计</button>
        </div>
      </span>
    </div>

    <div class="game-cards" id="gameCards"></div>

    <div class="grid half section-gap">
      <div class="card"><h3>情感对比</h3><div class="chart" id="chSentiCmp"></div></div>
      <div class="card"><h3>口碑对比</h3><div class="chart" id="chWordCmp"></div></div>
    </div>

    <div class="card section-gap">
      <div class="card-head">
        <h3>Top 主题对比（L2 · 观点粒度）</h3>
        <div class="seg sm" id="segPolar">
          <button data-polar="negative">负向</button>
          <button data-polar="positive">正向</button>
        </div>
      </div>
      <div class="grid three" id="topGrid"></div>
    </div>

    <div class="card section-gap">
      <h3>指标对比</h3>
      <div style="overflow:auto"><table class="tbl" id="tblKpi"></table></div>
    </div>`;

  const $ = id => document.getElementById(id);
  function paintSeg(container, attr, value) {
    container.querySelectorAll('button').forEach(b =>
      b.classList.toggle('active', b.dataset[attr] === value));
  }

  /* ---- 卡片渲染（meta 异步到位后重排重绘） ---- */
  function sortGames() {
    games = games.slice().sort((a, b) =>
      ((metaMap[b.target_id] || {}).release_date || '').localeCompare(
        (metaMap[a.target_id] || {}).release_date || '') ||
      a.name.localeCompare(b.name));
  }
  function renderCards() {
    $('gameCards').innerHTML = games.map(g => {
      const m = metaMap[g.target_id] || {};
      const appid = appidOf(g.target_id);
      return `
      <div class="game-card ${selected.has(g.target_id) ? 'selected' : ''}" data-tid="${esc(g.target_id)}">
        <div class="gc-cover" data-name="${esc(g.name)}">
          ${m.cover_file || m.release_date ? `<img src="/covers/${esc(appid)}.jpg" data-appid="${esc(appid)}" alt="${esc(g.name)}" onerror="__gcImgError(this)">` : '<div class="gc-loading">…</div>'}
        </div>
        <div class="gc-info">
          <div class="gc-name" title="${esc(g.name)}">${esc(g.name)}</div>
          <div class="gc-date">${esc(m.release_date || '…')}</div>
          <div class="gc-rating ${m.rating_desc ? ratingClass(m.rating_desc) : ''}">${esc(m.rating_desc || '…')}</div>
        </div>
      </div>`;
    }).join('');
  }

  /* ---- 元数据加载（stale-while-revalidate + 轮询，不阻塞首屏） ---- */
  async function loadMeta(tries = 0) {
    if (!$('gameCards')) return;  // 已切走路由，放弃轮询
    try {
      const d = await API.get(`/api/games/meta?targets=${encodeURIComponent(games.map(g => g.target_id).join(','))}`);
      metaMap = {};
      d.items.forEach(m => { metaMap[m.target_id] = m; });
      sortGames();
      renderCards();
      if (d.refreshing && d.refreshing.length && tries < 20) {
        metaTimer = setTimeout(() => loadMeta(tries + 1), 3000);
        return;
      }
    } catch (e) { /* meta 失败不阻塞看板，卡片用占位 */ }
    // 元数据到位（或放弃轮询）后刷新一次数据（发行日排序可能变化）
    if ($('gameCards')) refreshData();
  }

  /* ---- 卡片多选（至少保留 2，互不影响其他卡片） ---- */
  $('gameCards').addEventListener('click', e => {
    const card = e.target.closest('.game-card');
    if (!card) return;
    const tid = card.dataset.tid;
    if (selected.has(tid) && selected.size <= 2) { toast('至少保留 2 款游戏参与对比', true); return; }
    selected.has(tid) ? selected.delete(tid) : selected.add(tid);
    card.classList.toggle('selected', selected.has(tid));
    refreshData();
  });

  /* ---- 同期/累计 + 负向/正向 ---- */
  $('segMode').addEventListener('click', e => {
    const btn = e.target.closest('button[data-mode]');
    if (!btn || btn.dataset.mode === state.mode) return;
    state.mode = btn.dataset.mode;
    paintSeg($('segMode'), 'mode', state.mode);
    refreshData();
  });
  $('segPolar').addEventListener('click', e => {
    const btn = e.target.closest('button[data-polar]');
    if (!btn || btn.dataset.polar === state.polar) return;
    state.polar = btn.dataset.polar;
    paintSeg($('segPolar'), 'polar', state.polar);
    refreshData();
  });

  /* ---- 同期窗口（等长最近窗口，见文件头口径说明） ---- */
  async function computeWindow(sel) {
    const pre = await Promise.all(sel.map(g =>
      API.get(`/api/overview?target=${encodeURIComponent(g.target_id)}`).catch(() => null)));
    const latestDay = pre
      .filter(Boolean).map(o => (o.last_posted || '').slice(0, 10))
      .filter(Boolean).sort().pop();
    const releases = sel.map(g => (metaMap[g.target_id] || {}).release_date).filter(Boolean).sort();
    const maxRelease = releases[releases.length - 1];
    if (!latestDay || !maxRelease) return null;  // 数据不足 → 累计
    const D = daysBetween(maxRelease, latestDay);
    if (!(D > 0)) return null;
    return { start: addDays(latestDay, -D), end: latestDay, days: D };
  }

  /* ---- 数据拉取与渲染 ---- */
  async function fetchGame(g, win) {
    const base = { target: g.target_id };
    if (win) { base.start = win.start; base.end = win.end; }
    const qs = extra => {
      const p = new URLSearchParams({ ...base, ...extra });
      for (const [k, v] of [...p.entries()]) if (!v) p.delete(k);
      return p.toString();
    };
    const [oc, oo, topics] = await Promise.all([
      API.get(`/api/overview?${qs({ grain: 'comment' })}`),
      API.get(`/api/overview?${qs({ grain: 'opinion' })}`),
      API.get(`/api/topics?${qs({ level: 'L2', grain: 'opinion', sentiment: state.polar })}`),
    ]);
    return { g, oc, oo, topics };
  }

  function renderSentiCmp(list) {
    const p = Charts.palette();
    const names = list.map(x => x.g.name);
    // y 类目自下而上 → reverse 使卡片最左（最新发行）显示在最上；单条 bar 天然垂直居中
    const pct = (v, n) => n ? +(v / n * 100).toFixed(1) : 0;
    Charts.render('chSentiCmp', {
      legend: { top: 0, textStyle: { color: p.muted } },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: v => v + '%' },
      grid: { left: 12, right: 24, top: 32, bottom: 8, containLabel: true },
      xAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%', color: p.muted },
               splitLine: { show: false } },
      yAxis: {
        type: 'category', data: names.slice().reverse(),
        axisLabel: { color: p.muted, interval: 0, width: 96, overflow: 'break', lineHeight: 14 },
        axisTick: { show: false }, axisLine: { lineStyle: { color: p.line } },
      },
      series: ['正向', '中性', '负向'].map((label, i) => {
        const key = ['positive', 'neutral', 'negative'][i];
        const color = [p.pos, p.neu, p.neg][i];
        return {
          name: label, type: 'bar', stack: 's', barMaxWidth: 18, itemStyle: { color },
          data: list.map(x => {
            const s = x.oc.sentiment;
            const n = s.positive + s.neutral + s.negative;
            return pct(s[key], n);
          }).reverse(),
        };
      }),
    });
  }

  function renderWordCmp(list) {
    const p = Charts.palette();
    const pts = list
      .filter(x => x.oc.recommend_rate != null)
      .map(x => ({ value: [x.oc.total, x.oc.recommend_rate], name: x.g.name }));
    Charts.render('chWordCmp', {
      tooltip: {
        trigger: 'item',
        formatter: p2 => `${esc(p2.name)}<br>评论量：${fmtNum(p2.value[0])}<br>推荐率：${p2.value[1]}%`,
      },
      grid: { left: 12, right: 28, top: 28, bottom: 8, containLabel: true },
      xAxis: { type: 'value', name: '评论量', nameLocation: 'middle', nameGap: 24,
               nameTextStyle: { color: p.muted },
               axisLabel: { color: p.muted, formatter: v => fmtNum(v) }, splitLine: { show: false } },
      yAxis: { type: 'value', name: '推荐率%', min: 0, max: 100,
               nameTextStyle: { color: p.muted },
               axisLabel: { color: p.muted }, splitLine: { show: false } },
      series: [{
        type: 'scatter', symbolSize: 14,
        itemStyle: { color: p.primary, opacity: .8 },
        label: { show: true, formatter: p2 => p2.name, color: p.ink, position: 'top', fontSize: 11 },
        data: pts,
      }],
    });
  }

  function renderTopGrid(list) {
    const p = Charts.palette();
    const color = state.polar === 'negative' ? p.neg : p.pos;
    $('topGrid').innerHTML = list.map((x, i) => `
      <div class="card mini-topic">
        <h3 title="${esc(x.g.name)}">${esc(x.g.name)}</h3>
        <div class="mini-chart" id="top${i}"></div>
      </div>`).join('');
    list.forEach((x, i) => {
      const top5 = x.topics.slice(0, 5);
      if (!top5.length) {
        $(`top${i}`).innerHTML = '<div class="empty">该游戏无匹配情感的主题数据</div>';
        return;
      }
      // y 类目自下而上 → reverse 使 Top1 在最上
      Charts.render(`top${i}`, {
        tooltip: { trigger: 'item', formatter: '{b}：{c}' },
        grid: { left: 12, right: 34, top: 6, bottom: 6, containLabel: true },
        xAxis: { type: 'value', axisLabel: { show: false }, axisTick: { show: false },
                 axisLine: { show: false }, splitLine: { show: false } },
        yAxis: { type: 'category', data: top5.map(t => t.topic).reverse(),
                 axisLabel: { color: p.muted, width: 110, overflow: 'break', lineHeight: 13 },
                 axisTick: { show: false }, axisLine: { lineStyle: { color: p.line } } },
        series: [{
          type: 'bar', barMaxWidth: 14, data: top5.map(t => t.total).reverse(),
          itemStyle: { color, borderRadius: [0, 4, 4, 0] },
          label: { show: true, position: 'right', color: p.muted, fontSize: 11 },
        }],
      });
    });
  }

  function renderKpiTable(list, win) {
    const note = state.mode === '同期' && win
      ? `<tr><td colspan="7" class="empty" style="padding:8px 0;font-size:11.5px">同期口径：各游戏取等长的最近 ${win.days} 天窗口（N = 选中中最晚发行游戏的已发行天数；库内数据自 2026-07-31 起采集，「发行后前 N 天」历史窗口不可用，2026-09-04 调整）。</td></tr>`
      : (state.mode === '同期'
        ? '<tr><td colspan="7" class="empty" style="padding:8px 0;font-size:11.5px">同期窗口不可用（缺发行日期或库内数据），已回退累计口径。</td></tr>'
        : '');
    $('tblKpi').innerHTML = `
      <thead><tr>
        <th>游戏</th><th class="num">评论量</th><th class="num">推荐量</th><th class="num">推荐率</th>
        <th class="num">观点好评率</th><th class="num">发行天数</th><th>推荐评级（所有评测）</th>
      </tr></thead>
      <tbody>${list.map(x => {
        const rd = (metaMap[x.g.target_id] || {}).release_date;
        return `<tr>
          <td>${esc(x.g.name)}</td>
          <td class="num">${fmtNum(x.oc.total)}</td>
          <td class="num">${fmtNum(x.oc.recommend_count)}</td>
          <td class="num">${x.oc.recommend_rate ?? '-'}%</td>
          <td class="num">${x.oo.sentiment.positive_pct ?? '-'}%</td>
          <td class="num">${rd ? fmtNum(daysSince(rd)) : '-'}</td>
          <td><span class="gc-rating ${x.g.meta && x.g.meta.rating_desc ? ratingClass(x.g.meta.rating_desc) : ''}">${esc((metaMap[x.g.target_id] || {}).rating_desc || '-')}</span></td>
        </tr>`;
      }).join('')}</tbody>
      <tfoot>${note}</tfoot>`;
  }

  async function refreshData() {
    const seq = ++reqSeq;
    const sel = games.filter(g => selected.has(g.target_id));  // 卡片序
    if (!sel.length) return;
    try {
      const win = state.mode === '同期' ? await computeWindow(sel) : null;
      if (seq !== reqSeq) return;
      const list = await Promise.all(sel.map(g => fetchGame(g, win)));
      if (seq !== reqSeq) return;
      // 附 meta 供表格用
      list.forEach(x => { x.g.meta = metaMap[x.g.target_id] || {}; });
      renderSentiCmp(list);
      renderWordCmp(list);
      renderTopGrid(list);
      renderKpiTable(list, win);
    } catch (e) {
      if (seq === reqSeq) toast(e.message, true);
    }
  }

  paintSeg($('segMode'), 'mode', state.mode);
  paintSeg($('segPolar'), 'polar', state.polar);
  renderCards();
  loadMeta();          // 异步：不阻塞首屏；到位后重排卡片并刷新数据
  await refreshData();
};
