/* B站视频看板（2026-09-04 · 线框图重设计，风格对齐 dashboard/compare）
   数据源：/api/bilibili/videos（fetched 视频快照 + 采集量 + 性别分布 + 高光总结）
   区块：视频筛选 → 封面(点击弹窗播放)+视频信息 → 采集评论量/性别环形/情感环形
        → L1 主题分布（原声粒度，正向/负向拆分，点击联动筛选）+ 原声列表（likes 降序，等高滚动）
        → 弹幕时间轴（30s 固定桶平滑曲线，悬停高亮 + 浮层 10 条样本）→ 高光时刻（采集时 LLM 总结，三段排版） */
Routes.bilibili = async function (app) {
  const videos = await API.get('/api/bilibili/videos');
  if (!videos.length) { app.innerHTML = '<div class="empty">库中暂无已采集的 B 站视频，请先在「系统管理」添加 BV 号采集任务</div>'; return; }

  const state = {
    tid: videos[0].target_id,
    filterTopic: '',   // L1 联动筛选的主题
    filterSenti: '',   // 联动筛选携带的情感（positive/negative），与 topic 成对
    page: 1,
  };
  const PAGE_SIZE = 10;
  const cur = () => videos.find(v => v.target_id === state.tid) || videos[0];
  let reqSeq = 0;
  let dmLastIdx = -1;  // 弹幕悬停：仅桶切换时重建浮层（防卡顿）

  app.innerHTML = `
    <div class="page-head"><h1>B站视频看板</h1></div>
    <div class="toolbar">
      <select id="selVideo">
        ${videos.map(v => `<option value="${esc(v.target_id)}">${esc(v.title || v.bv_id)}</option>`).join('')}
      </select>
    </div>

    <div class="card video-hero">
      <div class="vh-cover" id="vhCover" title="点击播放">
        <img id="vhPic" alt="封面">
        <div class="vh-play">▶</div>
      </div>
      <div class="vh-info">
        <div class="vh-title"><a id="vhTitleLink" target="_blank" rel="noopener"></a></div>
        <div class="vh-sub">
          <span class="lc-lbl">UP主：</span><a id="vhOwnerLink" class="vh-owner" target="_blank" rel="noopener"></a>
          <span class="lc-lbl">投稿日期：</span><span id="vhPubdate"></span>
        </div>
        <div class="vh-tags" id="vhTags"></div>
        <div class="vh-stats" id="vhStats"></div>
      </div>
    </div>

    <div class="grid three section-gap">
      <div class="kpi-card kpi-xl" id="kpiCollected"></div>
      <div class="card"><h3>评论性别分布</h3><div class="chart ring-chart" id="chSex"></div></div>
      <div class="card"><h3>评论情感分布</h3><div class="chart ring-chart" id="chSentiB"></div></div>
    </div>

    <div class="grid cmp-grid section-gap">
      <div class="card l1-card">
        <h3>L1 主题分布</h3>
        <div class="chart-note">点击标签/条形可联动筛选右侧原声列表（再次点击取消）</div>
        <div class="l1-sub">正向情感</div>
        <div class="chart-note" id="notePos"></div>
        <div class="chart l1-chart" id="chL1Pos"></div>
        <div class="l1-sub">负向情感</div>
        <div class="chart-note" id="noteNeg"></div>
        <div class="chart l1-chart" id="chL1Neg"></div>
      </div>
      <div class="card voice-card">
        <div class="card-head"><h3 id="voiceTitle">原声列表</h3><span class="sub" id="voiceTotal" style="color:var(--muted);font-size:12px"></span></div>
        <div id="voiceList" class="voice-scroll"></div>
        <div class="pager">
          <button class="btn sm" id="pgPrev">上一页</button><span id="pgInfo"></span>
          <button class="btn sm" id="pgNext">下一页</button>
        </div>
      </div>
    </div>

    <div class="card section-gap">
      <h3>弹幕时间轴</h3>
      <div class="chart-wrap dm-wrap">
        <div class="dm-band" id="dmBand" hidden></div>
        <div class="chart" id="chDmB"></div>
        <div class="dm-pop" id="dmPop" hidden></div>
      </div>
    </div>

    <div class="section-gap">
      <h3 style="font-size:13.5px;font-weight:600;color:var(--ink2);margin-bottom:12px">高光时刻</h3>
      <div class="grid three" id="hlGrid"></div>
    </div>`;

  const $ = id => document.getElementById(id);
  const fmtSec = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  const RATE_COLOR = { 男: '--primary', 保密: '--muted', 女: '#b07be0' };
  // 大数 K/M 压缩（2026-09-05）：≥10 取整、<10 保留 1 位小数，不显示到个位数
  function fmtCompact(v) {
    if (v == null) return '-';
    if (v >= 1e6) { const n = v / 1e6; return (n >= 10 ? Math.floor(n) : +n.toFixed(1)) + 'M'; }
    if (v >= 1e3) { const n = v / 1e3; return (n >= 10 ? Math.floor(n) : +n.toFixed(1)) + 'K'; }
    return fmtNum(v);
  }

  /* ---- 视频信息区（封面点击 → 弹窗播放） ---- */
  function renderHero(v) {
    const pic = $('vhPic');
    pic.style.display = '';
    pic.dataset.fb = '0';
    // 本地封面优先（采集时下载到 data/covers/），失败回退 B 站 CDN
    pic.src = `/covers/${v.bv_id}.jpg`;
    pic.onerror = function () {
      if (this.dataset.fb === '0' && v.pic) { this.dataset.fb = '1'; this.src = v.pic; }
      else this.style.display = 'none';
    };

    const link = $('vhTitleLink');
    link.href = `https://www.bilibili.com/video/${v.bv_id}`;
    link.textContent = v.title || v.bv_id;

    const owner = $('vhOwnerLink');
    owner.href = v.owner_mid ? `https://space.bilibili.com/${v.owner_mid}` : '#';
    owner.textContent = v.owner_name || '未知UP主';

    $('vhPubdate').textContent = fmtDate(v.pubdate);
    $('vhTags').innerHTML = (v.tags || []).slice(0, 8)
      .map(t => `<span class="badge dim">${esc(t)}</span>`).join('');

    const stat = (a, b) => `<span class="vh-stat"><small>${a}</small>${b}</span>`;
    const sanlian = v.view ? ((v.like_count + v.coin + v.favorite) / v.view * 100).toFixed(1) + '%' : '-';
    $('vhStats').innerHTML =
      stat('播放量', fmtCompact(v.view)) +
      stat('评论量', fmtCompact(v.reply_total)) +
      stat('弹幕量', fmtCompact(v.danmaku_total)) +
      stat('点赞+收藏+投币比例', sanlian);
  }
  $('vhCover').addEventListener('click', () => {
    const v = cur();
    openModal(`
      <h2>${esc(v.title || v.bv_id)}</h2>
      <div class="bili-player">
        <iframe src="https://player.bilibili.com/player.html?bvid=${esc(v.bv_id)}&autoplay=0"
                style="width:100%;height:100%;border:0" allowfullscreen scrolling="no"></iframe>
      </div>`);
  });

  /* ---- 第三行：指标卡（对齐 dashboard 样式，字号更大） + 两个环形图 ---- */
  function renderKpi(v) {
    const collected = v.collected ? v.collected.comments : 0;
    const pctText = v.reply_total ? (collected / v.reply_total * 100).toFixed(1) + '%' : '-';
    $('kpiCollected').innerHTML = `
      <div class="kpi-label">采集评论量</div>
      <div class="kpi-value">${fmtNum(collected)}</div>
      <div class="kpi-sub">占原视频评论量 ${pctText}</div>`;
  }
  function ringChart(containerId, data) {
    const p = Charts.palette();
    Charts.render(containerId, {
      tooltip: { trigger: 'item', formatter: '{b}：{c}（{d}%）' },
      legend: { bottom: 0, textStyle: { color: p.muted }, itemGap: 12 },
      series: [{
        type: 'pie', radius: ['42%', '62%'], center: ['50%', '40%'],
        label: { color: p.muted, fontSize: 11, formatter: '{d}%', labelLine: { length: 8, length2: 10 } },
        data,
      }],
    });
  }
  function renderSex(v) {
    const s = v.sex || { male: 0, female: 0, unknown: 0 };
    ringChart('chSex', [
      { name: '男', value: s.male, itemStyle: { color: Charts.token(RATE_COLOR['男']) } },
      { name: '保密', value: s.unknown, itemStyle: { color: Charts.token(RATE_COLOR['保密']) } },
      { name: '女', value: s.female, itemStyle: { color: RATE_COLOR['女'] } },
    ]);
  }
  function renderSenti(v) {
    API.get(`/api/overview?target=${encodeURIComponent(state.tid)}`).then(ov => {
      const s = ov.sentiment;
      const p = Charts.palette();
      ringChart('chSentiB', [
        { name: '正向', value: s.positive, itemStyle: { color: p.pos } },
        { name: '中性', value: s.neutral, itemStyle: { color: p.neu } },
        { name: '负向', value: s.negative, itemStyle: { color: p.neg } },
      ]);
    }).catch(e => toast(e.message, true));
  }

  /* ---- 第四行左：L1 主题分布（正向/负向拆分，原声粒度，零填充固定顺序） ---- */
  // 交互：点击条形/标签 → 高亮（描边，不变色）并联动筛选原声列表（携带情感条件）；
  //       点同一个取消，点另一个（含跨图）切换。
  function drawL1Chart(containerId, senti, topics) {
    const p = Charts.palette();
    const color = senti === 'positive' ? p.pos : p.neg;
    // 「综合与元表达」已移到副标题备注（2026-09-05），条形只显示其余 9 个主题
    const bars = topics.filter(t => t.topic !== FB);
    Charts.render(containerId, {
      tooltip: { trigger: 'item', formatter: '{b}：{c}' },
      grid: { left: 12, right: 34, top: 4, bottom: 4, containLabel: true },
      xAxis: { type: 'value', axisLabel: { show: false }, axisTick: { show: false },
               axisLine: { show: false }, splitLine: { show: false } },
      yAxis: {
        type: 'category', data: bars.map(t => t.topic).reverse(),
        axisLabel: { color: p.muted, triggerEvent: true, width: 110, overflow: 'break', lineHeight: 13 },
        axisTick: { show: false }, axisLine: { lineStyle: { color: p.line } },
      },
      series: [{
        type: 'bar', barMaxWidth: 14,
        data: bars.map(t => ({
          value: t.total,
          itemStyle: t.topic === state.filterTopic && state.filterSenti === senti
            ? { color, borderRadius: [0, 4, 4, 0], borderColor: p.primary, borderWidth: 2 }  // 高亮=描边，不变色
            : { color, borderRadius: [0, 4, 4, 0] },
        })).reverse(),
        label: { show: true, position: 'right', color: p.muted, fontSize: 11 },
      }],
    });
    const chart = Charts.get(containerId);
    if (!chart) return;
    chart.off('click');
    chart.on('click', params => {
      let name = null;
      if (params.componentType === 'series') name = params.name;
      else if (params.targetType === 'axisLabel' && params.value != null) name = String(params.value);
      if (!name) return;
      if (state.filterTopic === name && state.filterSenti === senti) {
        state.filterTopic = ''; state.filterSenti = '';   // 再点取消
      } else {
        state.filterTopic = name; state.filterSenti = senti;  // 切换（跨图也是清除后重设）
      }
      state.page = 1;
      drawL1Chart('chL1Pos', 'positive', topicsCache.pos);
      drawL1Chart('chL1Neg', 'negative', topicsCache.neg);
      renderVoices();
    });
  }
  const topicsCache = { pos: [], neg: [] };
  const FB = '综合与元表达';
  function metaNote(topics) {
    // 与 dashboard L1 图同格式：「综合与元表达」N 条，占 x.x%（分母 = 该情感下的 L1 合计）
    const meta = topics.find(t => t.topic === FB) || null;
    const grand = topics.reduce((s, t) => s + t.total, 0);
    return meta ? `「${FB}」${fmtNum(meta.total)} 条，占 ${grand ? (meta.total / grand * 100).toFixed(1) : '0.0'}%` : '';
  }
  async function renderL1() {
    const base = { target: state.tid, level: 'L1', grain: 'comment', full: 'true' };
    const [pos, neg] = await Promise.all([
      API.get(`/api/topics?${new URLSearchParams({ ...base, sentiment: 'positive' })}`),
      API.get(`/api/topics?${new URLSearchParams({ ...base, sentiment: 'negative' })}`),
    ]);
    topicsCache.pos = pos;
    topicsCache.neg = neg;
    drawL1Chart('chL1Pos', 'positive', pos);
    drawL1Chart('chL1Neg', 'negative', neg);
    $('notePos').textContent = metaNote(pos);
    $('noteNeg').textContent = metaNote(neg);
    // 右侧原声列表高度与左卡对齐（左卡内容定高，右侧超出部分内部滚动）
    const l1 = document.querySelector('.l1-card'), vc = document.querySelector('.voice-card');
    if (l1 && vc) vc.style.height = l1.offsetHeight + 'px';
  }

  /* ---- 第四行右：原声列表（likes 降序 → 评论时间降序；联动筛选携带情感；等高滚动） ---- */
  function voiceCardHTML(c) {
    const ops = c.opinions || [];
    return `
    <div class="lcard" data-id="${c.id}">
      <div class="lcard-head">
        <div class="lc-main">
          <div class="lc-top">
            <span class="lc-id">#${c.id}</span>
            <span class="badge ${SENTI_BADGE[c.sentiment] || 'dim'}">${SENTI_LABEL[c.sentiment] || '未标注'}</span>
            <span class="cmt-likes">👍 ${fmtNum(c.likes)}</span>
          </div>
          <div class="lc-text">${esc(c.content)}</div>
          <div class="lc-meta">
            <span class="lc-lbl">评论时间</span>${fmtDate(c.posted_at)}
            <span class="badge dim">${esc(c.topic || '无主题')}</span>
          </div>
        </div>
      </div>
      <div class="lcard-detail">
        <div class="vd-cells">
          <div class="vd-cell"><span class="vd-lbl">点赞数</span><b>${fmtNum(c.likes)}</b></div>
          <div class="vd-cell"><span class="vd-lbl">回帖数</span><b>${fmtNum(c.replies)}</b></div>
        </div>
        <div class="vd-sub-label">观点列表</div>
        ${ops.length ? ops.map(op => `
          <div class="op-item">
            <div class="op-text">${esc(op.quote)}</div>
            <div class="op-tags">
              <span class="badge ${SENTI_BADGE[op.sentiment] || 'dim'}">${SENTI_LABEL[op.sentiment] || '未标注'}</span>
              <span class="badge dim">${esc((op.full_path || '').split('/').pop())}</span>
            </div>
          </div>`).join('') : '<div class="td-hint">该原声暂无观点</div>'}
      </div>
    </div>`;
  }
  async function renderVoices() {
    const seq = reqSeq;
    const params = new URLSearchParams({
      target: state.tid, grain: 'comment', sort: 'likes',
      topic: state.filterTopic, sentiment: state.filterSenti,
      page: state.page, page_size: PAGE_SIZE,
    });
    for (const [k, v] of [...params.entries()]) if (!v) params.delete(k);
    try {
      const d = await API.get(`/api/comments?${params}`);
      if (seq !== reqSeq) return;
      $('voiceList').innerHTML = d.items.length
        ? d.items.map(voiceCardHTML).join('')
        : `<div class="empty">${state.filterTopic ? `「${esc(state.filterTopic)}」下无原声` : '当前筛选下无原声'}</div>`;
      const f = [state.filterSenti && SENTI_LABEL[state.filterSenti], state.filterTopic]
        .filter(Boolean).join(' · ');
      $('voiceTotal').textContent = `共 ${fmtNum(d.total)} 条${f ? ` · ${f}` : ''}`;
      const maxPage = Math.max(1, Math.ceil(d.total / PAGE_SIZE));
      $('pgInfo').textContent = `${state.page} / ${maxPage}`;
      $('pgPrev').disabled = state.page <= 1;
      $('pgNext').disabled = state.page >= maxPage;
    } catch (e) {
      if (seq === reqSeq) $('voiceList').innerHTML = `<div class="empty">列表加载失败：${esc(e.message)}</div>`;
    }
  }
  $('voiceList').addEventListener('click', e => {
    const head = e.target.closest('.lcard-head');
    if (head) head.closest('.lcard').classList.toggle('expanded');
  });
  $('pgPrev').addEventListener('click', () => { if (state.page > 1) { state.page--; renderVoices(); } });
  $('pgNext').addEventListener('click', () => { state.page++; renderVoices(); });

  /* ---- 第五行：弹幕时间轴（平滑曲线 + 桶高亮 + 浮层，无默认图例） ---- */
  function renderDanmaku() {
    if (!$('chDmB')) return;  // 路由已离开（resize 监听兜底）
    API.get(`/api/danmaku/${encodeURIComponent(state.tid)}`).then(d => {
      const p = Charts.palette();
      const pop = $('dmPop'), band = $('dmBand');
      dmLastIdx = -1;
      if (!d.total) {
        band.hidden = true; pop.hidden = true;
        $('chDmB').querySelector('.empty')?.remove();  // 清理上次残留的空态占位
        Charts.render('chDmB', { xAxis: { show: false }, yAxis: { show: false }, series: [] });
        $('chDmB').insertAdjacentHTML('beforeend', '<div class="empty">该视频无弹幕数据</div>');
        return;
      }
      $('chDmB').querySelector('.empty')?.remove();
      Charts.render('chDmB', {
        legend: { show: false },  // 去除「弹幕」图例
        tooltip: { show: false },  // 自定义浮层替代默认 tooltip
        grid: { left: 12, right: 16, top: 30, bottom: 8, containLabel: true },
        xAxis: { type: 'category', data: d.buckets.map(b => fmtSec(b.start_sec)),
                 axisLabel: { color: p.muted }, axisTick: { show: false },
                 axisLine: { lineStyle: { color: p.line } } },
        yAxis: { type: 'value', name: '弹幕量', nameGap: 14,
                 axisLabel: { color: p.muted }, nameTextStyle: { color: p.muted, align: 'right', padding: [0, 6, 0, 0] },
                 splitLine: { show: false } },
        series: [{
          type: 'line', smooth: true, symbol: 'circle', symbolSize: 5, name: '弹幕量',
          data: d.buckets.map(b => b.count),
          lineStyle: { color: p.primary, width: 2.5 },
          itemStyle: { color: p.primary },
          areaStyle: { color: p.primary, opacity: .15 },
        }],
      });
      const chart = Charts.get('chDmB');
      if (!chart) return;
      const n = d.buckets.length;
      const hide = () => { pop.hidden = true; band.hidden = true; };
      const showBucket = (idx) => {
        const b = d.buckets[idx];
        if (!b) { hide(); return; }
        // 高亮带 + 浮层：仅桶切换时重排一次（悬停高频路径零 JS 重排，消除卡顿）
        if (idx !== dmLastIdx) {
          dmLastIdx = idx;
          const x1 = chart.convertToPixel({ xAxisIndex: 0 }, idx);
          const x2 = chart.convertToPixel({ xAxisIndex: 0 }, idx + 1);
          band.style.left = x1 + 'px';
          band.style.width = Math.max(8, x2 - x1) + 'px';
          band.hidden = false;
          pop.innerHTML = `
            <div class="dm-pop-title">${fmtSec(b.start_sec)} ~ ${fmtSec(b.end_sec)} · ${b.count} 条</div>
            ${(b.samples || []).map(s => `<div class="dm-pop-item">${esc(s)}</div>`).join('') || '<div class="dm-pop-item dim">无样本</div>'}`;
          const wrapW = $('chDmB').clientWidth;
          pop.style.left = Math.max(4, Math.min(x1 - 110, wrapW - 268)) + 'px';
          pop.style.top = '8px';
        }
        pop.hidden = false;
      };
      // 桶热区：每桶一层透明覆盖 div（整个矩形区域可触发，不要求压到折线）
      const host = $('chDmB');
      host.querySelectorAll('.dm-hot').forEach(el => el.remove());
      const xs = [];
      for (let i = 0; i <= n; i++) xs.push(chart.convertToPixel({ xAxisIndex: 0 }, i));
      for (let i = 0; i < n; i++) {
        const hot = document.createElement('div');
        hot.className = 'dm-hot';
        hot.style.left = xs[i] + 'px';
        hot.style.width = Math.max(2, xs[i + 1] - xs[i]) + 'px';
        hot.addEventListener('mouseenter', () => showBucket(i));
        hot.addEventListener('mouseleave', hide);
        host.appendChild(hot);
      }
      // 窗口尺寸变化后热区与曲线错位 → 重画（挂 window，路由离开时由空元素守卫兜底）
      if (window.__biliDmResize) window.removeEventListener('resize', window.__biliDmResize);
      window.__biliDmResize = () => { if ($('chDmB')) renderDanmaku(); };
      window.addEventListener('resize', window.__biliDmResize);
    }).catch(e => toast(e.message, true));
  }

  /* ---- 第六行：高光时刻（LLM 输出 概括/示例/整体 → 前端重排为 总结/氛围/示例） ---- */
  function renderSummary(text) {
    const lines = String(text || '').split(/\n+/).filter(Boolean);
    const sec = {}; const other = [];
    lines.forEach(line => {
      const m = line.match(/^(概括|示例|整体)[:：]\s*(.*)$/);
      if (m) sec[m[1]] = m[2]; else other.push(line);
    });
    // 展示顺序：总结(概括) → 氛围(整体) → 示例
    const order = [['概括', '总结'], ['整体', '氛围'], ['示例', '示例']];
    let html = '';
    for (const [k, label] of order) {
      if (sec[k]) html += `<p class="hl-sec"><b>${label}</b>${esc(sec[k])}</p>`;
    }
    other.forEach(l => { html += `<p class="hl-sec">${esc(l)}</p>`; });
    return html || '<p class="hl-sec">暂无总结</p>';
  }
  function renderHighlights(v) {
    const buckets = (v.highlights && v.highlights.buckets) || [];
    $('hlGrid').innerHTML = buckets.length ? buckets.map(b => `
      <div class="card hl-card">
        <div class="hl-time">${fmtSec(b.start_sec)} ~ ${fmtSec(b.end_sec)} · ${fmtNum(b.count)} 条弹幕</div>
        <div class="hl-summary">${renderSummary(b.summary)}</div>
      </div>`).join('')
      : '<div class="empty" style="grid-column:1/-1">高光总结尚未生成（采集回填后显示）</div>';
  }

  /* ---- 刷新编排 ---- */
  function renderVideo() {
    const v = cur();
    renderHero(v);
    renderKpi(v);
    renderSex(v);
    renderSenti(v);
    renderHighlights(v);
  }
  function refreshAll() {
    const seq = ++reqSeq;
    state.filterTopic = ''; state.filterSenti = ''; state.page = 1; dmLastIdx = -1;
    renderVideo();
    Promise.all([
      renderL1().catch(e => { if (seq === reqSeq) toast(e.message, true); }),
      renderVoices(),
      renderDanmaku(),
    ]);
  }

  $('selVideo').addEventListener('change', e => { state.tid = e.target.value; refreshAll(); });
  refreshAll();
};
