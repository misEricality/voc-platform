/* 单游戏看板：游戏下拉 + 全局时间筛选 + 评论量/推荐率 + 评论趋势
   + 原声/观点颗粒度看板（情感饼图 + L1 全量固定顺序条形图）
   + 可折叠原声/观点列表（posted_at 降序，每页 10 条，独立展开）
   口径见 docs/architecture/WEB_DASHBOARD.md §4：时间字段 = posted_at；
   原声主题 = comments.topic（主观点 L1）；观点主题 = comment_opinions.full_path L1 段 */
Routes.dashboard = async function (app) {
  const targets = await API.get('/api/targets?platform=steam&monitored=true');
  if (!targets.length) { app.innerHTML = '<div class="empty">库中暂无 Steam 游戏数据，请先在「系统管理」添加采集任务</div>'; return; }

  // 游戏下拉顺序与 compare 左起顺序一致（发行日期倒序，数据来自 /api/games/meta；失败回落名称序）
  let ordered = targets.slice();
  try {
    const d = await API.get(`/api/games/meta?targets=${encodeURIComponent(targets.map(t => t.target_id).join(','))}`);
    const rd = {};
    d.items.forEach(m => { rd[m.target_id] = m.release_date || ''; });
    ordered.sort((a, b) =>
      (rd[b.target_id] || '').localeCompare(rd[a.target_id] || '') ||
      a.name.localeCompare(b.name));
  } catch (e) {
    ordered.sort((a, b) => a.name.localeCompare(b.name));
  }

  // URL 参数（compare 指标表跳入）：#/dashboard?target=<target_id>&range=30d
  const urlq = new URLSearchParams(location.hash.split('?')[1] || '');
  const urlTarget = ordered.some(t => t.target_id === urlq.get('target')) ? urlq.get('target') : null;

  /* ---- 单一状态源 ---- */
  const PAGE_SIZE = 10;
  const state = {
    target: urlTarget || ordered[0].target_id,   // Steam appid
    range: urlq.get('range') === '30d' ? '30d' : 'all',  // all | 30d | 7d | 1d | custom
    start: '', end: '',             // range=custom 时生效（YYYY-MM-DD 闭区间）
    grain: 'comment',               // comment(原声) | opinion(观点)
    senti: '', topic: '',           // 仅作用于下方列表
    page: 1,
  };
  let reqSeq = 0;                   // 竞态守卫：快速切换时丢弃过期响应

  app.innerHTML = `
    <div class="pg-dashboard">
    <div class="page-head">
      <h1>Steam游戏看板 - 单游戏</h1>
      <span class="head-actions"><button class="btn sm" id="btnBack">返回</button></span>
    </div>

    <div class="range-wrap">
      <select id="selGame">
        ${ordered.map(t => `<option value="${esc(t.target_id)}"${t.target_id === state.target ? ' selected' : ''}>${esc(t.name)}</option>`).join('')}
      </select>
      <div class="range-right">
        <div class="seg" id="segRange">
          <button data-range="all">所有时间</button>
          <button data-range="30d">近30天</button>
          <button data-range="7d">近7天</button>
          <button data-range="1d">近1天</button>
          <button data-range="custom" id="btnCustom">自选时间</button>
        </div>
        <span class="range-label" id="rangeLabel"></span>
      </div>
      <div class="daterange-panel" id="drPanel">
        <div class="dr-row">
          <label>起始</label><input type="date" id="dpStart">
          <label>结束</label><input type="date" id="dpEnd">
        </div>
        <div class="dr-hint" id="drErr"></div>
        <div class="dr-actions">
          <button class="btn sm" id="dpClear">清空</button>
          <button class="btn sm primary" id="dpOk">确定</button>
        </div>
      </div>
    </div>

    <div class="kpi-trend section-gap">
      <div class="kpi-card" id="kpiCount"><div class="kpi-label">评论量</div><div class="kpi-value">-</div></div>
      <div class="kpi-card" id="kpiRate"><div class="kpi-label">推荐率</div><div class="kpi-value">-</div></div>
      <div class="card trend-card">
        <div class="card-head"><h3>评论趋势</h3></div>
        <div class="chart-wrap">
          <div class="chart" id="chTrend"></div>
          <div class="chart-overlay empty" id="trendEmpty" hidden>当前时间窗内无数据</div>
        </div>
      </div>
    </div>

    <div class="board-head section-gap">
      <h2 id="boardTitle">原声看板</h2>
      <div class="seg sm" id="segGrain">
        <button data-grain="comment">原声</button>
        <button data-grain="opinion">观点</button>
      </div>
    </div>
    <div class="grid half">
      <div class="card"><h3>情感分布</h3><div class="chart" id="chSenti"></div></div>
      <div class="card">
        <h3>L1 主题分布</h3>
        <div class="chart-note" id="l1Note"></div>
        <div class="chart" id="chL1"></div>
      </div>
    </div>

    <div class="card section-gap">
      <div class="card-head">
        <h3 id="listTitle">原声列表</h3>
        <div class="toolbar head-tools">
          <select id="selSenti">
            <option value="">全部情感</option>
            <option value="positive">正向</option>
            <option value="neutral">中性</option>
            <option value="negative">负向</option>
          </select>
          <div class="treedrop" id="tdTopic">
            <button class="btn sm" id="tdTrigger">全部主题</button>
            <div class="treedrop-panel" id="tdPanel"></div>
          </div>
          <span class="sub" id="listTotal" style="color:var(--muted);font-size:12px"></span>
        </div>
      </div>
      <div id="listBody"></div>
      <div class="pager">
        <button class="btn sm" id="pgPrev">上一页</button>
        <span id="pgInfo"></span>
        <button class="btn sm" id="pgNext">下一页</button>
      </div>
    </div>
    </div>`;

  /* ---- 小工具 ---- */
  const $ = id => document.getElementById(id);
  function dstr(offsetDays) {
    const d = new Date(); d.setDate(d.getDate() - offsetDays);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  // 快捷档换算：「近 N 天」不含当天（2026-09-03 需求变更）——近1天=昨天单日；近7天=昨天往前7天
  function windowParams() {
    if (state.range === 'custom') return { start: state.start, end: state.end };
    if (state.range === '30d') return { start: dstr(30), end: dstr(1) };
    if (state.range === '7d') return { start: dstr(7), end: dstr(1) };
    if (state.range === '1d') return { start: dstr(1), end: dstr(1) };
    return { start: '', end: '' };
  }
  function qs(extra = {}) {
    const w = windowParams();
    const p = new URLSearchParams({ target: state.target, ...w, ...extra });
    for (const [k, v] of [...p.entries()]) if (v === '' || v == null) p.delete(k);
    return p.toString();
  }
  function fmtHours(minutes) {
    if (minutes == null) return '-';
    return (Number(minutes) / 60).toFixed(1) + ' 小时';
  }
  function voteBadge(rating) {
    if (rating === 1) return '<span class="v-vote up">推荐</span>';
    if (rating === 0) return '<span class="v-vote down">不推荐</span>';
    return '';
  }
  const pathL1 = p => (p || '').split('/')[0];
  const pathL3 = p => (p || '').split('/').pop();
  function paintSeg(container, attr, value) {
    container.querySelectorAll('button').forEach(b =>
      b.classList.toggle('active', b.dataset[attr] === value));
  }

  /* ---- 时间筛选器（互斥 active + 内联日期面板 + 范围文本） ---- */
  function syncRangeLabel(firstLast) {
    const w = windowParams();
    $('rangeLabel').textContent = (w.start && w.end)
      ? `${w.start} 至 ${w.end}`
      : (firstLast || '');  // 所有时间 → 库内实际数据边界（overview 回填）
  }
  $('segRange').addEventListener('click', e => {
    const btn = e.target.closest('button[data-range]');
    if (!btn) return;
    if (btn.dataset.range === 'custom') { toggleDatePanel(); return; }
    state.range = btn.dataset.range;
    paintSeg($('segRange'), 'range', state.range);
    $('drPanel').classList.remove('open');
    refreshGlobal();
  });
  function toggleDatePanel(force) {
    const p = $('drPanel');
    const open = force !== undefined ? force : !p.classList.contains('open');
    if (open) {
      const w = windowParams();
      $('dpStart').value = w.start || dstr(6);
      $('dpEnd').value = w.end || dstr(1);
      $('drErr').textContent = '';
    }
    p.classList.toggle('open', open);
  }
  document.addEventListener('click', e => {
    if (!e.target.closest('.range-wrap')) $('drPanel').classList.remove('open');
    if (!e.target.closest('#tdTopic')) $('tdTopic').classList.remove('open');
  });
  $('dpOk').addEventListener('click', () => {
    const s = $('dpStart').value, e = $('dpEnd').value;
    if (!s || !e) { $('drErr').textContent = '请选择起始与结束日期'; return; }
    if (s > e) { $('drErr').textContent = '起始日期不能晚于结束日期'; return; }
    if ((new Date(e) - new Date(s)) / 86400000 > 365) { $('drErr').textContent = '时间跨度不能超过 365 天'; return; }
    state.range = 'custom'; state.start = s; state.end = e;
    paintSeg($('segRange'), 'range', 'custom');
    $('drPanel').classList.remove('open');
    refreshGlobal();
  });
  $('dpClear').addEventListener('click', () => {
    state.range = 'all';
    paintSeg($('segRange'), 'range', 'all');
    $('drPanel').classList.remove('open');
    refreshGlobal();
  });

  /* ---- 返回按钮：回游戏对比看板（compare 默认选中发行日倒序前 3 款） ---- */
  $('btnBack').addEventListener('click', () => {
    location.hash = '#/compare';
  });

  /* ---- 指标卡 + 评论趋势 + 看板（overview 一次喂 KPI 与情感饼图） ---- */
  async function renderOverview(seq) {
    const ov = await API.get(`/api/overview?${qs({ grain: state.grain })}`);
    if (seq !== reqSeq) return;
    const s = ov.sentiment;
    $('kpiCount').innerHTML = `
      <div class="kpi-label">评论量</div>
      <div class="kpi-value">${fmtNum(ov.total)}</div>`;
    $('kpiRate').innerHTML = `
      <div class="kpi-label">推荐率</div>
      <div class="kpi-value kpi-rate-pos">${ov.recommend_rate ?? '-'}%</div>`;
    syncRangeLabel(
      ov.first_posted ? `${fmtDate(ov.first_posted)} 至 ${fmtDate(ov.last_posted)}` : '');
    renderPie(s);
  }

  function renderPie(s) {
    const p = Charts.palette();
    Charts.render('chSenti', {
      tooltip: { trigger: 'item', formatter: '{b}：{c}（{d}%）' },
      legend: { bottom: 0, textStyle: { color: p.muted }, itemGap: 18 },
      series: [{
        type: 'pie', radius: ['52%', '78%'], center: ['50%', '44%'],
        label: { color: p.muted, formatter: '{d}%' },
        // 固定顺序：正 → 中 → 负
        data: [
          { name: '正向', value: s.positive, itemStyle: { color: p.pos } },
          { name: '中性', value: s.neutral, itemStyle: { color: p.neu } },
          { name: '负向', value: s.negative, itemStyle: { color: p.neg } },
        ],
      }],
    });
  }

  async function renderTrend(seq) {
    const d = await API.get(`/api/trends?${qs()}`);
    if (seq !== reqSeq) return;
    const p = Charts.palette();
    const items = d.items;
    // 空态走覆盖层，绝不改写图表容器 DOM（修复：覆盖层曾 destroy canvas 导致图表永久消失）
    $('trendEmpty').hidden = items.length > 0;
    if (!items.length) {
      Charts.render('chTrend', { xAxis: { type: 'category', data: [] }, yAxis: { type: 'value' }, series: [] });
      return;
    }
    Charts.render('chTrend', {
      legend: { top: 0, textStyle: { color: p.muted } },
      tooltip: { trigger: 'axis' },
      grid: { left: 12, right: 16, top: 36, bottom: 8, containLabel: true },
      xAxis: { type: 'category', data: items.map(i => i.day), axisLabel: { color: p.muted } },
      yAxis: [
        { type: 'value', name: '评论量', axisLabel: { color: p.muted }, splitLine: { lineStyle: { color: Charts.token('--line2') } }, nameTextStyle: { color: p.muted } },
        { type: 'value', name: '推荐率%', min: 0, max: 100, axisLabel: { color: p.muted }, splitLine: { show: false }, nameTextStyle: { color: p.muted } },
      ],
      series: [
        { name: '评论量', type: 'line', smooth: true, symbol: 'circle', symbolSize: 5, yAxisIndex: 0,
          data: items.map(i => i.total),
          lineStyle: { color: p.primary, width: 2.5 }, itemStyle: { color: p.primary } },
        { name: '推荐率', type: 'line', smooth: true, symbol: 'none', yAxisIndex: 1,
          data: items.map(i => i.recommend_rate), connectNulls: false,  // null 断裂防误导
          lineStyle: { color: p.pos, width: 2, type: 'dashed' },
          itemStyle: { color: p.pos } },  // 图例图标用绿（缺省会继承全局 color[0] 蓝）
      ],
    });
  }

  async function renderBoardCharts(seq) {
    const topics = await API.get(`/api/topics?${qs({ level: 'L1', grain: state.grain, full: 'true' })}`);
    if (seq !== reqSeq) return;
    const p = Charts.palette();
    // 「综合与元表达」移出条形图 → 标题下备注（2026-09-03 需求变更）
    const fb = (TREE && TREE.fallback) || '综合与元表达';
    const meta = topics.find(t => t.topic === fb) || null;
    const bars = meta ? topics.filter(t => t !== meta) : topics;
    const grand = topics.reduce((s, t) => s + t.total, 0);
    $('l1Note').textContent = meta
      ? `「${fb}」${fmtNum(meta.total)} 条，占 ${grand ? (meta.total / grand * 100).toFixed(1) : '0.0'}%`
      : '';
    // ECharts yAxis 类目首个在底部 → reverse 保证 yaml primary 第一条「机制与内容」在最上
    Charts.render('chL1', {
      tooltip: { trigger: 'item', formatter: '{b}：{c}' },
      grid: { left: 12, right: 34, top: 4, bottom: 4, containLabel: true },
      // 不显示横轴与数据刻度：数值只读条形右侧标签
      xAxis: { type: 'value', axisLabel: { show: false }, axisTick: { show: false },
               axisLine: { show: false }, splitLine: { show: false } },
      yAxis: { type: 'category', data: bars.map(t => t.topic).reverse(),
               axisLabel: { color: p.muted }, axisTick: { show: false },
               axisLine: { lineStyle: { color: p.line } } },
      series: [{
        type: 'bar', barMaxWidth: 16, data: bars.map(t => t.total).reverse(),
        itemStyle: { color: p.primary, borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', color: p.muted, fontSize: 11 },
      }],
    });
  }

  /* ---- 颗粒度切换：标题 + 饼图 + 条形图 + 列表 ---- */
  function applyGrainUI() {
    $('boardTitle').textContent = state.grain === 'comment' ? '原声看板' : '观点看板';
    $('listTitle').textContent = state.grain === 'comment' ? '原声列表' : '观点列表';
    renderTreedrop();
  }
  $('segGrain').addEventListener('click', e => {
    const btn = e.target.closest('button[data-grain]');
    if (!btn || btn.dataset.grain === state.grain) return;
    state.grain = btn.dataset.grain;
    state.topic = '';  // 颗粒度切换后主题筛选失效（原声只有 L1），重置
    state.page = 1;
    paintSeg($('segGrain'), 'grain', state.grain);
    refreshCharts();
    refreshList();
  });

  /* ---- 列表筛选器（作用于下方列表） ---- */
  $('selSenti').addEventListener('change', e => { state.senti = e.target.value; state.page = 1; refreshList(); });

  let TREE = null;  // /api/topics/tree 模块级缓存，切换颗粒度不重拉
  async function loadTree() {
    if (!TREE) TREE = await API.get('/api/topics/tree');
    return TREE;
  }
  function renderTreedrop() {
    const panel = $('tdPanel');
    if (!TREE) { panel.innerHTML = '<div class="td-hint">加载中…</div>'; return; }
    if (state.grain === 'comment') {
      panel.innerHTML = `<div class="td-item" data-path="">全部主题</div>` +
        TREE.primary.map(l1 => `<div class="td-item td-l1" data-path="${esc(l1)}">${esc(l1)}</div>`).join('');
    } else {
      const rows = [`<div class="td-item" data-path="">全部主题</div>`];
      for (const [l1, subs] of Object.entries(TREE.hierarchy)) {
        rows.push(`<div class="td-item td-l1" data-path="${esc(l1)}">${esc(l1)}</div>`);
        for (const [l2, l3s] of Object.entries(subs || {})) {
          const l2path = `${l1}/${l2}`;
          rows.push(`<div class="td-item td-l2" data-path="${esc(l2path)}">${esc(l2)}</div>`);
          for (const l3 of l3s || []) {
            rows.push(`<div class="td-item td-l3" data-path="${esc(l2path)}/${esc(l3)}">${esc(l3)}</div>`);
          }
        }
      }
      panel.innerHTML = rows.join('');
    }
    markTreedropActive();
  }
  function markTreedropActive() {
    $('tdPanel').querySelectorAll('.td-item').forEach(el =>
      el.classList.toggle('active', el.dataset.path === state.topic));
    const cur = $('tdPanel').querySelector('.td-item.active');
    $('tdTrigger').textContent = cur ? cur.textContent : '全部主题';
  }
  $('tdTrigger').addEventListener('click', e => {
    e.stopPropagation();
    $('tdTopic').classList.toggle('open');
  });
  $('tdPanel').addEventListener('click', e => {
    const item = e.target.closest('.td-item');
    if (!item) return;
    state.topic = item.dataset.path;
    state.page = 1;
    markTreedropActive();
    $('tdTopic').classList.remove('open');
    refreshList();
  });

  /* ---- 可折叠列表（原声 / 观点；posted_at 降序；每页 10 条；独立展开） ---- */
  function voiceCardHTML(c) {
    const ops = c.opinions || [];
    return `
    <div class="lcard" data-id="${c.id}">
      <div class="lcard-head">
        <div class="lc-main">
          <div class="lc-top">
            <span class="lc-id">#${c.id}</span>
            <span class="badge ${SENTI_BADGE[c.sentiment] || 'dim'}">${SENTI_LABEL[c.sentiment] || '未标注'}</span>
          </div>
          <div class="lc-text">${esc(c.content)}</div>
          <div class="lc-meta">
            <span class="lc-lbl">评论时间</span>${fmtDate(c.posted_at)}
            <span class="badge dim">${esc(c.topic || '无主题')}</span>
          </div>
        </div>
        <div class="lc-side">
          ${voteBadge(c.rating)}
          <div class="lc-play"><small>已游玩</small>${fmtHours(c.extra && c.extra.playtime_at_review)}</div>
        </div>
      </div>
      <div class="lcard-detail">
        <div class="vd-cells">
          <div class="vd-cell"><span class="vd-lbl">点赞数</span><b>${fmtNum(c.likes)}</b></div>
          <div class="vd-cell"><span class="vd-lbl">回帖数</span><b>${fmtNum(c.replies)}</b></div>
          <div class="vd-cell"><span class="vd-lbl">累计游玩</span><b>${fmtHours(c.extra && c.extra.playtime_forever)}</b></div>
        </div>
        <div class="vd-sub-label">观点列表</div>
        ${ops.length ? ops.map(op => `
          <div class="op-item">
            <div class="op-text">${esc(op.quote)}</div>
            <div class="op-tags">
              <span class="badge ${SENTI_BADGE[op.sentiment] || 'dim'}">${SENTI_LABEL[op.sentiment] || '未标注'}</span>
              <span class="badge dim">${esc(pathL3(op.full_path))}</span>
            </div>
          </div>`).join('') : '<div class="td-hint">该原声暂无观点</div>'}
      </div>
    </div>`;
  }
  function opinionCardHTML(op) {
    const c = op.comment;
    return `
    <div class="lcard" data-id="${op.id}">
      <div class="lcard-head">
        <div class="lc-main">
          <div class="lc-top">
            <span class="lc-id">#${op.id}</span>
            <span class="badge ${SENTI_BADGE[op.sentiment] || 'dim'}">${SENTI_LABEL[op.sentiment] || '未标注'}</span>
          </div>
          <div class="lc-text">${esc(op.quote)}</div>
          <div class="lc-meta">
            <span class="lc-lbl">评论时间</span>${fmtDate(c && c.posted_at)}
            <span class="badge dim">${esc(pathL1(op.full_path))}</span>
          </div>
        </div>
        <div class="lc-side">
          ${voteBadge(c && c.rating)}
          <div class="lc-play"><small>已游玩</small>${fmtHours(c && c.extra && c.extra.playtime_at_review)}</div>
        </div>
      </div>
      <div class="lcard-detail">
        <div class="vd-sub-label">评论原文</div>
        ${c ? `
        <div class="op-item">
          <div class="op-tags">
            <span class="badge ${SENTI_BADGE[c.sentiment] || 'dim'}">${SENTI_LABEL[c.sentiment] || '未标注'}</span>
            <span class="badge dim">${esc(c.topic || '无主题')}</span>
          </div>
          <div class="op-text">${esc(c.content)}</div>
        </div>` : '<div class="td-hint">所属原声已不存在</div>'}
      </div>
    </div>`;
  }
  /* 溢出文本悬停显示全文；未溢出时不设置 title（避免无意义浮窗） */
  function setTextOverflowTooltips(root, selector = '.lc-text,.op-text') {
    root.querySelectorAll(selector).forEach(el => {
      el.title = '';
      if (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1) {
        el.title = el.textContent.trim();
      }
    });
  }

  function renderPager(total) {
    const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
    $('pgInfo').textContent = `${state.page} / ${maxPage}`;
    $('pgPrev').disabled = state.page <= 1;
    $('pgNext').disabled = state.page >= maxPage;
  }
  async function renderList() {
    const seq = reqSeq;  // 与图表共用竞态 token：任何全局刷新都会使旧列表响应过期
    try {
      let d;
      if (state.grain === 'comment') {
        d = await API.get(`/api/comments?${qs({
          grain: 'comment', sentiment: state.senti, topic: state.topic,
          page: state.page, page_size: PAGE_SIZE,
        })}`);
        $('listBody').innerHTML = d.items.length
          ? d.items.map(voiceCardHTML).join('')
          : '<div class="empty">当前筛选下无原声</div>';
      } else {
        d = await API.get(`/api/opinions?${qs({
          sentiment: state.senti, topic: state.topic,
          page: state.page, page_size: PAGE_SIZE,
        })}`);
        $('listBody').innerHTML = d.items.length
          ? d.items.map(opinionCardHTML).join('')
          : '<div class="empty">当前筛选下无观点</div>';
      }
      if (seq !== reqSeq) return;
      setTextOverflowTooltips($('listBody'));
      $('listTotal').textContent = `共 ${fmtNum(d.total)} 条`;
      renderPager(d.total);
    } catch (e) {
      if (seq === reqSeq) $('listBody').innerHTML = `<div class="empty">列表加载失败：${esc(e.message)}</div>`;
    }
  }
  // 独立展开：只切当前卡片，不影响其他卡片
  $('listBody').addEventListener('click', e => {
    const head = e.target.closest('.lcard-head');
    if (head) head.closest('.lcard').classList.toggle('expanded');
  });
  $('pgPrev').addEventListener('click', () => { if (state.page > 1) { state.page--; refreshList(); } });
  $('pgNext').addEventListener('click', () => { state.page++; refreshList(); });

  /* ---- 刷新编排（竞态守卫） ---- */
  async function refreshCharts() {
    const seq = ++reqSeq;
    try {
      await Promise.all([renderOverview(seq), renderTrend(seq), renderBoardCharts(seq)]);
    } catch (e) { if (seq === reqSeq) toast(e.message, true); }
  }
  async function refreshList() {
    await renderList();  // renderList 内部用 reqSeq 防过期
  }
  async function refreshGlobal() {
    state.page = 1;  // 游戏/时间变化 → 列表回第一页
    await Promise.all([refreshCharts(), renderList()]);
  }

  $('selGame').addEventListener('change', e => { state.target = e.target.value; refreshGlobal(); });
  paintSeg($('segRange'), 'range', state.range);
  paintSeg($('segGrain'), 'grain', state.grain);
  loadTree().then(renderTreedrop).catch(() => { /* 树加载失败不阻塞主看板 */ });

  await refreshGlobal();
};
