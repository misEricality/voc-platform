/* 路由 + 启动（hash 路由，零依赖；Routes 注册表声明在 api.js，先于页面脚本加载） */
const PageTitles = {
  dashboard: '单游戏看板', compare: '游戏对比看板', bilibili: 'B站视频看板',
  data: '系统管理 - 数据管理', admin: '系统管理 - 采集任务',
};

async function renderRoute() {
  let page = (location.hash.replace(/^#\//, '') || 'dashboard').split('?')[0];
  if (page === 'trends') { location.hash = '#/data'; return; }  // 旧链接兼容
  if (!Routes[page]) { location.hash = '#/dashboard'; return; }

  // 导航高亮
  document.querySelectorAll('#nav a').forEach(a =>
    a.classList.toggle('active', a.dataset.page === page));

  Charts.disposeAll();
  const app = document.getElementById('app');
  app.innerHTML = '<div class="loading">加载中…</div>';
  try {
    await Routes[page](app);
  } catch (e) {
    app.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
    toast(e.message, true);
  }
  document.title = `${PageTitles[page] || page} · 灵听 Lynx`;
}

async function initMeta() {
  try {
    const h = await API.get('/api/health');
    document.getElementById('dbMeta').textContent = `库内评论 ${fmtNum(h.comments)} 条 · 实时读取`;
  } catch (e) {
    document.getElementById('dbMeta').textContent = 'API 不可达';
  }
}

window.addEventListener('hashchange', renderRoute);
(async function boot() {
  await initMeta();
  await renderRoute();
  setInterval(initMeta, 60_000);  // 顶栏数据量每分钟刷新
})();
