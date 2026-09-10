/* API 请求封装：统一 {ok, data} 包装 + 错误 toast */
/* 页面路由注册表（必须先于 pages/*.js 求值：const 会因 TDZ 让先加载的页面脚本赋值报错） */
var Routes = {};

/* ---- 静态快照模式（方案③，scripts/ops/export_static_snapshot.py 生成）----
   静态版入口 index.html 注入 window.STATIC_SNAPSHOT=true 与 SNAPSHOT_META；
   GET 请求改读 /snapshot/ 下的预生成 JSON（manifest 路由表：端点+参数→文件）。
   未收录的筛选组合抛出明确错误 → 页面现有 catch/toast 降级。实时模式零影响。 */
const SNAPSHOT_ROUTES_P = window.STATIC_SNAPSHOT
  ? fetch('snapshot/manifest.json').then(r => {
      if (!r.ok) throw new Error('快照 manifest 加载失败：HTTP ' + r.status);
      return r.json();
    })
  : null;

function __snapshotMatch(routes, path, params) {
  const keys = Object.keys(params);
  return routes.find(r => {
    if (r.path !== path) return false;
    const rk = Object.keys(r.params);
    if (rk.length !== keys.length) return false;
    return rk.every(k => r.params[k] === '*' || r.params[k] === params[k]);
  });
}

async function snapshotGet(path) {
  const m = await SNAPSHOT_ROUTES_P;
  const qIdx = path.indexOf('?');
  const ep = qIdx === -1 ? path : path.slice(0, qIdx);
  const params = {};
  if (qIdx !== -1) {
    new URLSearchParams(path.slice(qIdx + 1))
      .forEach((v, k) => { if (v !== '') params[k] = v; });
  }
  const hit = __snapshotMatch(m.routes, ep, params);
  if (!hit) {
    throw new Error('静态快照未收录该查询（' + ep + '）——仅含预生成维度');
  }
  const r = await fetch('snapshot/' + hit.file);
  if (!r.ok) throw new Error('快照文件加载失败：HTTP ' + r.status);
  return r.json();
}

const API = (() => {
  async function request(path, opts = {}) {
    if (window.STATIC_SNAPSHOT && (!opts.method || opts.method === 'GET')) {
      return snapshotGet(path);
    }
    const r = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store',  // 看板要实时：禁浏览器启发式缓存（配合服务端 Cache-Control: no-store）
      ...opts,
    });
    let body = null;
    try { body = await r.json(); } catch (e) { /* non-json */ }
    if (!r.ok || !body || body.ok !== true) {
      const detail = (body && (body.detail || body.error)) || `HTTP ${r.status}`;
      const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      err.status = r.status;
      throw err;
    }
    return body.data;
  }
  return {
    get: (path) => request(path),
    post: (path, data) => request(path, { method: 'POST', body: JSON.stringify(data || {}) }),
    patch: (path, data) => request(path, { method: 'PATCH', body: JSON.stringify(data || {}) }),
    del: (path) => request(path, { method: 'DELETE' }),
  };
})();

/* 工具 */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
const SENTI_LABEL = { positive: '正向', neutral: '中性', negative: '负向' };
const SENTI_BADGE = { positive: 'pos', neutral: 'neu', negative: 'neg' };
const STATUS_BADGE = { '采集中': 'info', '待采集': 'neu', '已采集': 'pos', '已暂停': 'dim', '采集失败': 'neg' };
function fmtAxis(v) { const n = Number(v); if (v == null || isNaN(n)) return ""; const a = Math.abs(n); if (a >= 1e6) return (n / 1e6).toFixed(1) + "M"; if (a >= 1e3) return (n / 1e3).toFixed(1) + "K"; return String(n); }
function fmtNum(v) { return (v === null || v === undefined) ? '-' : Number(v).toLocaleString('zh-CN'); }
function fmtDate(t) { return t ? String(t).slice(0, 10) : '-'; }
function pct(a, b) { return b ? (a / b * 100).toFixed(1) + '%' : '0%'; }

function toast(msg, isErr, dur = 2200) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('err', !!isErr);
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), dur);
}

/* 模态框 */
function openModal(html) {
  closeModal();
  const root = document.getElementById('modalRoot');
  root.innerHTML = `<div class="modal-mask" data-mask><div class="modal">${html}</div></div>`;
  root.querySelector('[data-mask]').addEventListener('click', e => {
    if (e.target === e.currentTarget) closeModal();
  });
  document.addEventListener('keydown', modalEsc);
  return root.querySelector('.modal');
}
function modalEsc(e) { if (e.key === 'Escape') closeModal(); }
function closeModal() {
  document.getElementById('modalRoot').innerHTML = '';
  document.removeEventListener('keydown', modalEsc);
}
