/* 路由 + 启动（hash 路由，零依赖；Routes 注册表声明在 api.js，先于页面脚本加载） */
const PageTitles = {
  // 2026-09-10：AI 一级页 #/agent 已下线归档（pages/archive/），入口统一为右下悬浮球
  dashboard: 'Steam游戏看板 - 单游戏', compare: 'Steam游戏看板 - 游戏对比', bilibili: 'B站视频看板',
  data: '系统管理 - 数据管理', admin: '系统管理 - 采集任务',
};

/* 导航分组：顶层项在其子页时也点亮（2026-09-09） */
const PAGE_GROUP = { compare: 'steam', dashboard: 'steam', bilibili: 'bilibili', admin: 'admin', data: 'admin' };

async function renderRoute() {
  // 默认首页=compare（2026-09-10：agent 一级页下线，恢复 compare 为默认首页；
  // 旧链接 #/agent 与 #/agent/<id> 自动落到 compare，对话历史走悬浮球大窗查看）
  const defaultPage = 'compare';
  // 2026-09-10 修复：page 只取第一段——#/agent/<session_id> 必须解析成 agent 路由，
  // 此前 split 后是 'agent/<id>'，Routes 表无此 key → 重定向回 #/agent，
  // 表现为「点历史会话无反应」（被弹回新对话页），Routes.agent 内部的 sessionId 提取永远走不到
  let page = ((location.hash.replace(/^#\//, '') || defaultPage).split('?')[0]).split('/')[0];
  if (page === 'trends') { location.hash = '#/data'; return; }  // 旧链接兼容
  if (!Routes[page]) { location.hash = `#/${defaultPage}`; return; }

  // 导航高亮：顶层项（不在 .nav-menu 下拉里的）按分组点亮，下拉子项精确匹配
  const group = PAGE_GROUP[page];
  document.querySelectorAll('#nav a').forEach(a => {
    const isTop = !a.closest('.nav-menu');
    const on = isTop ? PAGE_GROUP[a.dataset.page] === group : a.dataset.page === page;
    a.classList.toggle('active', on);
  });

  Charts.disposeAll();
  const app = document.getElementById('app');
  app.innerHTML = '<div class="loading">加载中…</div>';
  try {
    await Routes[page](app);
  } catch (e) {
    app.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
    toast(e.message, true);
  }
  document.title = `${PageTitles[page] || page} · Lynx`;
}

async function initMeta() {
  if (window.STATIC_SNAPSHOT) {
    const m = window.SNAPSHOT_META || {};
    document.getElementById('dbMeta').textContent =
      `静态快照 · ${m.generated_at || ''} 生成 · 库内评论 ${fmtNum(m.comments)} 条`;
    return;
  }
  try {
    const h = await API.get('/api/health');
    document.getElementById('dbMeta').textContent = `库内评论 ${fmtNum(h.comments)} 条 · 实时读取`;
  } catch (e) {
    document.getElementById('dbMeta').textContent = 'API 不可达';
  }
}

window.addEventListener('hashchange', renderRoute);
(async function boot() {
  // 1) 挂全局 AI 抽屉（跨页可见，右下悬浮球 + 抽屉）
  //    必须先于 initMeta：因为 initMeta 失败也会让抽屉可见——解耦
  if (!window.STATIC_SNAPSHOT) {  // 静态快照版不挂（导出时不打 AI）
    try { window.mountAgentDrawer && window.mountAgentDrawer(); }
    catch (e) { console.warn('mountAgentDrawer failed', e); }
  }

  await initMeta();
  await renderRoute();
  if (!window.STATIC_SNAPSHOT) {
    setInterval(initMeta, 60_000);  // 顶栏数据量每分钟刷新（静态快照模式无需轮询）
  }
})();
