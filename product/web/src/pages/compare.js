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
  let userTouched = false;  // 用户手动改过选择后，meta 到位不再覆盖默认选中
  const MAX_SELECTED = 6;   // 最多同时选 6 款参与对比
  // 静态快照模式（window.STATIC_SNAPSHOT）：同期窗口依赖运行时动态计算，
  // 预生成 JSON 无法覆盖任意选中组合 → 强制累计口径并隐藏同期按钮
  const state = { mode: window.STATIC_SNAPSHOT ? '累计' : '同期', polar: 'negative' };  // 同期 | 累计；negative | positive
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

  // 封面兜底链（2026-09-08 横版化）：本地 /covers（header_schinese）→ CDN 简中 header → CDN 英文 header → 名称占位
  window.__gcImgError = function (img) {
    const step = +(img.dataset.fb || 0);
    const chain = [
      `https://cdn.cloudflare.steamstatic.com/steam/apps/${img.dataset.appid}/header_schinese.jpg`,
      `https://cdn.cloudflare.steamstatic.com/steam/apps/${img.dataset.appid}/header.jpg`,
    ];
    if (step < chain.length) {
      img.dataset.fb = step + 1;
      img.src = chain[step];
    } else {
      img.style.display = 'none';
      img.closest('.gc-cover').classList.add('noimg');
    }
  };

  app.innerHTML = `
    <div class="page-head">
      <h1>Steam游戏看板 - 游戏对比</h1>
      <span class="head-actions">
        <div class="seg sm" id="segMode">
          ${window.STATIC_SNAPSHOT ? '' : '<button data-mode="同期" title="同期：各游戏取等长的最近 N 天窗口（N = 选中中最晚发行游戏的已发行天数，按库内最新评论日截止）">同期</button>'}
          <button data-mode="累计" title="累计：不截断时间，使用库内全部数据对比">累计</button>
        </div>
      </span>
    </div>

    <div class="gc-row" id="gcRow">
      <button class="gs-arrow" id="gcPrev" aria-label="向左滑动">‹</button>
      <div class="game-cards" id="gameCards"></div>
      <button class="gs-arrow" id="gcNext" aria-label="向右滑动">›</button>
    </div>

    <div class="grid half section-gap">
      <div class="card"><h3>情感对比</h3><div class="chart" id="chSentiCmp"></div></div>
      <div class="card"><h3>口碑对比</h3><div class="chart" id="chWordCmp"></div></div>
    </div>

    <div class="card section-gap">
      <div class="card-head">
        <h3>Top 主题对比</h3>
        <div class="seg sm" id="segPolar">
          <button data-polar="negative">负向</button>
          <button data-polar="positive">正向</button>
        </div>
      </div>
      <div class="grid three" id="topGrid"></div>
    </div>

    <div class="card section-gap">
      <div class="card-head">
        <h3>评论词云对比</h3>
      </div>
      <div class="grid three" id="cloudGrid"></div>
    </div>

    <div class="card section-gap">
      <h3>指标对比</h3>
      <div style="overflow:auto"><table class="tbl fixed" id="tblKpi"></table></div>
    </div>
    <button class="backtop" id="btnTop" title="回到页面顶部" hidden>↑ 顶部</button>`;

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
      const date = esc(m.release_date || '…');
      const rating = esc(m.rating_desc || '…');
      return `
      <div class="game-card ${selected.has(g.target_id) ? 'selected' : ''}" data-tid="${esc(g.target_id)}">
        <div class="gc-cover" data-name="${esc(g.name)}">
          ${m.cover_file || m.release_date ? `<img src="/covers/${esc(appid)}.jpg" data-appid="${esc(appid)}" alt="${esc(g.name)}" onerror="__gcImgError(this)">` : '<div class="gc-loading">…</div>'}
          <div class="gc-tip">
            <span class="gc-tip-name">${esc(g.name)}</span>
            <span class="gc-tip-meta">${date} · ${rating}</span>
          </div>
        </div>
        <div class="gc-info">
          <div class="gc-name" title="${esc(g.name)}">${esc(g.name)}</div>
          <div class="gc-meta">
            <span class="gc-date">${date}</span>
            <span class="gc-rating ${m.rating_desc ? ratingClass(m.rating_desc) : ''}">${rating}</span>
          </div>
        </div>
      </div>`;
    }).join('');
    updateCardVis();
  }

  /* ---- 吸顶行（gc-row）：滚过卡片行后冻结在顶栏下，信息行折叠只留横版封面；
     hover 封面浮层显示名称/发行日/评级。
     吸顶位跟随 topbar 可见性：topbar 不在视口顶部（被隐藏/滚出）时贴 top:0 ---- */
  function updateCardVis() {
    // 仅维护箭头可用态（2026-09-10：offscreen 变暗已移除——横向滚动被容器裁剪，
    // 卡片永远不会滑出浏览器视口，无需可视性区分）
    const wrap = $('gameCards');
    if (!wrap) return;
    $('gcPrev').disabled = wrap.scrollLeft <= 2;
    $('gcNext').disabled = wrap.scrollLeft >= wrap.scrollWidth - wrap.clientWidth - 2;
  }
  const gcStep = () => {
    const c = $('gameCards').querySelector('.game-card');
    return c ? (c.getBoundingClientRect().width + 14) * 2 : 400;
  };
  $('gcPrev').addEventListener('click', () => $('gameCards').scrollBy({ left: -gcStep(), behavior: 'smooth' }));
  $('gcNext').addEventListener('click', () => $('gameCards').scrollBy({ left: gcStep(), behavior: 'smooth' }));
  $('gameCards').addEventListener('scroll', updateCardVis, { passive: true });

  /* ---- 冻结检测 + 吸顶位跟随 topbar + 回顶按钮 ---- */
  const topbarEl = document.querySelector('.topbar');
  function syncSticky() {
    const row = $('gcRow');
    if (!row) return;
    const top = topbarEl && topbarEl.getBoundingClientRect().bottom > 1 ? 60 : 0;
    row.style.top = top + 'px';
    row.classList.toggle('frozen', row.getBoundingClientRect().top <= top + 1);
  }
  window.addEventListener('scroll', syncSticky, { passive: true });
  const btnTop = $('btnTop');
  if (btnTop) {
    window.addEventListener('scroll', () => { btnTop.hidden = window.scrollY < 600; }, { passive: true });
    btnTop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
  }
  window.addEventListener('resize', updateCardVis);

  /* ---- 元数据加载（stale-while-revalidate + 轮询，不阻塞首屏） ---- */
  async function loadMeta(tries = 0) {
    if (!$('gameCards')) return;  // 已切走路由，放弃轮询
    try {
      const d = await API.get(`/api/games/meta?targets=${encodeURIComponent(games.map(g => g.target_id).join(','))}`);
      metaMap = {};
      d.items.forEach(m => { metaMap[m.target_id] = m; });
      sortGames();
      // 默认选中跟随发行日排序重算（新游戏如「明末」排到第一 → 默认选中新前 3）；
      // 用户已手动改过选择则不覆盖
      if (!userTouched) {
        selected.clear();
        games.slice(0, 3).forEach(g => selected.add(g.target_id));
      }
      renderCards();
      if (!window.STATIC_SNAPSHOT && d.refreshing && d.refreshing.length && tries < 20) {
        metaTimer = setTimeout(() => loadMeta(tries + 1), 3000);
        return;
      }
    } catch (e) { /* meta 失败不阻塞看板，卡片用占位 */ }
    // 元数据到位（或放弃轮询）后刷新一次数据（发行日排序可能变化）
    if ($('gameCards')) refreshData();
  }

  /* ---- 卡片多选（至少保留 2 / 最多选 6，互不影响其他卡片） ---- */
  $('gameCards').addEventListener('click', e => {
    const card = e.target.closest('.game-card');
    if (!card) return;
    const tid = card.dataset.tid;
    if (selected.has(tid) && selected.size <= 2) { toast('至少保留 2 款游戏参与对比', true); return; }
    if (!selected.has(tid) && selected.size >= MAX_SELECTED) { toast(`最多同时选择 ${MAX_SELECTED} 款游戏参与对比`, true); return; }
    userTouched = true;
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
    setAgentContext();
  });
  $('segPolar').addEventListener('click', e => {
    const btn = e.target.closest('button[data-polar]');
    if (!btn || btn.dataset.polar === state.polar) return;
    state.polar = btn.dataset.polar;
    paintSeg($('segPolar'), 'polar', state.polar);
    refreshData();
    setAgentContext();
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
      xAxis: { type: 'value', name: '评论量', nameLocation: 'middle', nameGap: 28,
               nameTextStyle: { color: p.muted },
               axisLabel: { color: p.muted, formatter: v => fmtAxis(v) }, splitLine: { show: false } },
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
    // 跨图联动：悬停任一条形/标签 → 所有游戏图表中同名 L2 高亮（含 blur 压暗其余项）
    const miniCharts = [], miniMaps = [];
    const highlight = name => {
      miniCharts.forEach((ch, k) => {
        ch.dispatchAction({ type: 'downplay' });
        const idx = miniMaps[k].get(name);
        if (idx != null) {
          // 含该标签：高亮同名项，其余由 focus:self 压暗
          ch.setOption({ series: [{ itemStyle: { opacity: 1 } }] }, { silent: true });
          ch.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: idx });
        } else {
          // TOP5 不含该标签：不亮任何项，5 个标签全部暗淡
          ch.setOption({ series: [{ itemStyle: { opacity: 0.3 } }] }, { silent: true });
        }
      });
    };
    const clearHl = () => miniCharts.forEach(ch => {
      ch.dispatchAction({ type: 'downplay' });
      ch.setOption({ series: [{ itemStyle: { opacity: 1 } }] }, { silent: true });
    });
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
                 axisLabel: { color: p.muted, width: 110, overflow: 'break', lineHeight: 13, triggerEvent: true },
                 axisTick: { show: false }, axisLine: { lineStyle: { color: p.line } } },
        series: [{
          type: 'bar', barMaxWidth: 14, data: top5.map(t => t.total).reverse(),
          itemStyle: { color, borderRadius: [0, 4, 4, 0] },
          emphasis: { focus: 'self', itemStyle: { borderColor: p.primary, borderWidth: 2 } },
          blur: { itemStyle: { opacity: .3 } },
          label: { show: true, position: 'right', color: p.muted, fontSize: 11 },
        }],
      });
      const ch = Charts.get(`top${i}`);
      if (!ch) return;
      miniCharts.push(ch);
      miniMaps.push(new Map(top5.map(t => t.topic).reverse().map((name, j) => [name, j])));
      ch.on('mouseover', params => {
        const name = params.componentType === 'series' ? params.name
          : (params.targetType === 'axisLabel' && params.value != null ? String(params.value) : null);
        if (name) highlight(name);
      });
      ch.on('mouseout', clearHl);
    });
  }

  function renderKpiTable(list) {
    $('tblKpi').innerHTML = `
      <thead><tr>
        <th style="width:17%">游戏</th>
        <th class="num" style="width:12%">评论量</th>
        <th class="num" style="width:12%">推荐量</th>
        <th class="num" style="width:11%">推荐率</th>
        <th class="num" style="width:13%">观点好评率</th>
        <th class="num" style="width:13%">发行天数</th>
        <th class="th-c" style="width:22%">推荐评级（所有评测）</th>
      </tr></thead>
      <tbody>${list.map(x => {
        const rd = (metaMap[x.g.target_id] || {}).release_date;
        return `<tr>
          <td><a href="#/dashboard?target=${encodeURIComponent(x.g.target_id)}&range=30d"
                 title="查看「${esc(x.g.name)}」单游戏看板（近30天）">${esc(x.g.name)}</a></td>
          <td class="num">${fmtNum(x.oc.total)}</td>
          <td class="num">${fmtNum(x.oc.recommend_count)}</td>
          <td class="num">${x.oc.recommend_rate ?? '-'}%</td>
          <td class="num">${x.oo.sentiment.positive_pct ?? '-'}%</td>
          <td class="num">${rd ? fmtNum(daysSince(rd)) : '-'}</td>
          <td class="td-c"><span class="gc-rating ${x.g.meta && x.g.meta.rating_desc ? ratingClass(x.g.meta.rating_desc) : ''}">${esc((metaMap[x.g.target_id] || {}).rating_desc || '-')}</span></td>
        </tr>`;
      }).join('')}</tbody>`;
  }

  async function refreshData() {
    const seq = ++reqSeq;
    const sel = games.filter(g => selected.has(g.target_id));  // 卡片序
    if (!sel.length) return;
    try {
      let win = state.mode === '同期' ? await computeWindow(sel) : null;
      if (seq !== reqSeq) return;
      if (state.mode === '同期' && !win) {
        // 同期窗口不可用（缺发行日期或库内数据）→ 红色告警 5s + 口径弹回累计
        toast('同期窗口不可用（缺发行日期或库内数据），已切换为累计口径', true, 5000);
        state.mode = '累计';
        paintSeg($('segMode'), 'mode', state.mode);
      }
      const list = await Promise.all(sel.map(g => fetchGame(g, win)));
      if (seq !== reqSeq) return;
      // 附 meta 供表格用
      list.forEach(x => { x.g.meta = metaMap[x.g.target_id] || {}; });
      renderSentiCmp(list);
      renderWordCmp(list);
      renderTopGrid(list);
      renderKpiTable(list);
      renderCloudGrid(sel, win, seq);  // 词云独立拉取，不阻塞主图表
    } catch (e) {
      if (seq === reqSeq) toast(e.message, true);
    }
  }

  /* ---- 评论词云对比（2026-09-08）：跟随选中游戏与同期/累计窗口；不受负向/正向切换影响 ----
     字号 = 跨游戏 TF-IDF 区分度；颜色 = 词的主导情感（后端聚合该词所属评论的情感分布） */
  async function renderCloudGrid(sel, win, seq) {
    const grid = $('cloudGrid');
    if (!grid) return;
    grid.innerHTML = sel.map((g, i) => `
      <div class="card mini-topic">
        <h3 title="${esc(g.name)}">${esc(g.name)}</h3>
        <div class="cloud-chart" id="cloud${i}"></div>
      </div>`).join('');
    let data = null;
    try {
      const qs = new URLSearchParams({ targets: sel.map(g => g.target_id).join(',') });
      if (win) { qs.set('start', win.start); qs.set('end', win.end); }
      data = await API.get(`/api/wordcloud?${qs}`);
      if (seq !== reqSeq) return;  // 过期响应丢弃
    } catch (e) { /* 静态快照未收录 / 接口失败 → 空态 */ }
    if (seq !== reqSeq) return;
    if (!data || !data.items || !data.items.length) {
      grid.innerHTML = '<div class="empty">词云数据不可用</div>';
      return;
    }
    const byTid = {};
    data.items.forEach(it => { byTid[it.target_id] = it; });
    const p = Charts.palette();
    const colorMap = { positive: p.pos, negative: p.neg, neutral: '#8b95a0' };
    sel.forEach((g, i) => {
      const el = $(`cloud${i}`);
      const it = byTid[g.target_id];
      if (!el) return;
      if (!it || !it.words.length) {
        el.innerHTML = '<div class="empty">时间窗内无足够评论</div>';
        return;
      }
      // echarts-wordcloud 内部处理会丢掉 data 项的自定义字段 → tooltip 用词名查表取数
      const byWord = {};
      it.words.forEach(w => { byWord[w.word] = w; });
      Charts.render(`cloud${i}`, {
        tooltip: {
          formatter: p2 => {
            const w = byWord[p2.name];
            if (!w) return esc(p2.name);
            // 好评/差评取占比更高的一项（与词颜色同口径）
            const good = w.pos_pct >= w.neg_pct;
            return `${esc(p2.name)}<br>出现 ${fmtNum(w.count)} 次 · 占该游戏词频 ${w.share}%` +
              ` · ${good ? '好评' : '差评'}率 ${good ? w.pos_pct : w.neg_pct}%`;
          },
        },
        series: [{
          type: 'wordCloud', shape: 'circle',
          width: '98%', height: '98%',
          sizeRange: [12, 34], rotationRange: [0, 0], gridSize: 6,
          drawOutOfBound: false, layoutAnimation: true,  // 逐词飘入动画（2026-09-09 恢复）
          data: it.words.map(w => ({
            name: w.word,
            value: w.weight,
            textStyle: { color: colorMap[w.sentiment] || colorMap.neutral },
          })),
        }],
      });
    });
  }

  /* ---- 跨页上下文（2026-09-09 阶段 8 落地）：compare 是多游戏对比，无单一 quick_query；
     只暴露选中列表 + polar/mode 让 agent 知道当前对比的视角 ---- */
  function setAgentContext() {
    const sel = games.filter(g => selected.has(g.target_id));
    window.__pageAgentContext = {
      page: 'compare',
      mode: state.mode,        // 同期 / 累计
      polar: state.polar,      // negative / positive
      selected: sel.map(g => ({ target_id: g.target_id, name: g.name })),
      label: `${state.mode} · ${state.polar === 'negative' ? '负向' : '正向'} · ${sel.length} 款游戏`,
      // 多游戏对比无单一 quick_query；agent-drawer 不会显示"引用当前查询"按钮
      /* 2026-09-10「引用当前查询」：选中游戏的对比 KPI 摘要（每游戏一行） */
      build_summary: async () => {
        const enc = encodeURIComponent;
        if (!sel.length) throw new Error('未选中任何游戏');
        const rows = await Promise.all(sel.map(g =>
          API.get(`/api/overview?target=${enc(g.target_id)}&grain=comment`).catch(() => null)));
        const lines = [];
        rows.forEach((o, i) => {
          const g = sel[i];
          if (!o) { lines.push(`${g.name || g.target_id}: 数据获取失败`); return; }
          const s = o.sentiment || {};
          lines.push(`${o.name || g.name || g.target_id}: 总 ${o.total} · 负 ${s.negative}(${s.negative_pct}%) · 正 ${s.positive} · 推荐率 ${o.recommend_rate ?? '-'}% · 最近评论 ${(o.last_posted || '').slice(0, 10) || '-'}`);
        });
        return [
          `页面: compare（游戏对比看板）`,
          `口径: ${state.mode === 'window' ? '同期窗口' : '累计'} · 视角 ${state.polar === 'negative' ? '负向' : '正向'} · 共 ${sel.length} 款`,
          ...lines,
        ].join('\n');
      },
    };
  }

  paintSeg($('segMode'), 'mode', state.mode);
  paintSeg($('segPolar'), 'polar', state.polar);
  renderCards();
  syncSticky();        // 初始吸顶位与冻结态对齐
  loadMeta();          // 异步：不阻塞首屏；到位后重排卡片并刷新数据
  await refreshData();
  setAgentContext();   // baseline（即便 meta 加载失败也有 mode/polar）
};
