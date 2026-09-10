/* Agent API 客户端（2026-09-09 第 5 轮）
   - sessions CRUD：create / list / get / delete
   - SSE chat 流式：fetch + ReadableStream，解析 event: xxx\ndata: {...}
   - export Markdown：触发浏览器下载
   - 所有请求注入 X-Anon-User-Id（来自 utils/anon-id.js 暴露的 window.getAnonUserId）

   通过 window.Agent = { Sessions, chatStream, exportMarkdown } 暴露。 */
(function () {
  const HEADERS = () => ({
    'Content-Type': 'application/json',
    'X-Anon-User-Id': window.getAnonUserId(),
  });

  /* ---- 通用 fetch 封装 ---- */
  async function _json(path, opts) {
    opts = opts || {};
    const r = await fetch(path, {
      ...opts,
      headers: Object.assign({}, HEADERS(), opts.headers || {}),
      credentials: 'same-origin',
      cache: 'no-store',
    });
    let body = null;
    try { body = await r.json(); } catch (e) { /* 非 JSON */ }
    if (!r.ok || !body || body.ok !== true) {
      const detail = (body && (body.detail || body.error)) || ('HTTP ' + r.status);
      const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      err.status = r.status;
      throw err;
    }
    return body.data;
  }

  /* ---- Sessions CRUD ---- */
  const Sessions = {
    create: (body) =>
      _json('/api/agent/sessions', {
        method: 'POST',
        body: JSON.stringify(body || {}),
      }),

    list: (params) => {
      params = params || {};
      const q = new URLSearchParams({ limit: params.limit || 30, offset: params.offset || 0 });
      if (params.page) q.set('page', params.page);
      return _json('/api/agent/sessions?' + q);
    },

    get: (sid) => _json('/api/agent/sessions/' + encodeURIComponent(sid)),

    del: (sid) =>
      _json('/api/agent/sessions/' + encodeURIComponent(sid), { method: 'DELETE' }),
  };

  /* ---- SSE chat 流式 ---- */
  function _parseSseFrame(frame) {
    let eventName = 'message';
    let dataLine = '';
    const lines = frame.split('\n');
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.indexOf('event: ') === 0) eventName = line.slice(7).trim();
      else if (line.indexOf('data: ') === 0) dataLine += line.slice(6);
    }
    if (!dataLine) return null;
    try { return { event: eventName, data: JSON.parse(dataLine) }; }
    catch (e) { return { event: eventName, data: { _raw: dataLine, _parse_error: true } }; }
  }

  async function* chatStream(body) {
    const r = await fetch('/api/agent/chat', {
      method: 'POST',
      headers: HEADERS(),
      credentials: 'same-origin',
      cache: 'no-store',
      body: JSON.stringify(body || {}),
    });

    if (!r.ok || !r.body) {
      let msg = 'HTTP ' + r.status;
      try {
        const j = await r.json();
        msg = (j && (j.detail || j.error)) || msg;
      } catch (e) { /* ignore */ }
      yield { event: 'error', data: { message: msg } };
      return;
    }

    const reader = r.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buf = '';
    try {
      while (true) {
        const rd = await reader.read();
        if (rd.done) break;
        buf += decoder.decode(rd.value, { stream: true });
        let sep;
        while ((sep = buf.indexOf('\n\n')) !== -1) {
          const raw = buf.slice(0, sep);
          buf = buf.slice(sep + 2);
          const ev = _parseSseFrame(raw);
          if (ev) yield ev;
        }
      }
      if (buf.trim()) {
        const ev = _parseSseFrame(buf.trim());
        if (ev) yield ev;
      }
    } finally {
      try { reader.releaseLock(); } catch (e) { /* ignore */ }
    }
  }

  /* ---- Markdown 导出 ---- */
  async function exportMarkdown() {
    const r = await fetch('/api/agent/export', {
      headers: HEADERS(),
      credentials: 'same-origin',
      cache: 'no-store',
    });
    if (!r.ok) {
      let msg = 'HTTP ' + r.status;
      try { const j = await r.json(); msg = (j && (j.detail || j.error)) || msg; }
      catch (e) { /* ignore */ }
      throw new Error(msg);
    }
    const disp = r.headers.get('Content-Disposition') || '';
    const m = disp.match(/filename="?([^";]+)"?/);
    const filename = m ? m[1] : 'lynx_agent_history_' + Date.now() + '.md';
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 100);
  }

  /* ---- Markdown 渲染（assistant 消息用；2026-09-10）----
     marked（vendor/marked.min.js）+ DOMPurify（vendor/purify.min.js）净化防 XSS。
     任何一步缺失/异常都降级为纯文本 esc()，保证永不因渲染挂掉丢消息。 */
  function renderAgentMd(text) {
    const raw = String(text == null ? '' : text);
    if (!raw) return '';
    try {
      if (!window.marked || !window.DOMPurify) return esc(raw);
      const html = window.marked.parse(raw, { breaks: true, gfm: true });
      return window.DOMPurify.sanitize(html, {
        FORBID_TAGS: ['style', 'form', 'input', 'iframe'],
        ADD_ATTR: ['target'],
      });
    } catch (e) {
      return esc(raw);
    }
  }
  window.renderAgentMd = renderAgentMd;

  window.Agent = { Sessions, chatStream, exportMarkdown };
})();
