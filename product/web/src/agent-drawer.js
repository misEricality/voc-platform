/* 原声分析 Agent · 全局抽屉（2026-09-10 两段式重构）
   - 右下悬浮球：跨页可见；点击展开**小窗**（无论之前是什么状态）
   - 两段式窗口：
     · 小窗 420×75vh —— 仅消息流 + 输入
     · 大窗 ~900×800 —— 左侧历史对话列表（原 #/agent 一级页功能迁入，该页已归档）+ 右侧对话
   - 头部箭头按钮：小窗态「左上箭头」→ 扩大；大窗态「右下箭头」→ 缩回
   - 动效：展开渐现 + 关闭渐灭（小/大窗均生效）
   - 头部「引用当前查询」：仅 dashboard/bilibili/compare 生效（页面注入 build_summary），
     引用后跨页/关浮窗保留，仅点「清除数据引用」清空；引用数据随请求 context 传后端（不落库）
   - done 事件 usage：消息流尾部展示本轮 token 消耗小字
   - main.js 在 boot 阶段调用 window.mountAgentDrawer() */
(function () {
  const CSS_HREF = 'src/agent-drawer.css?v=20260910c';

  const SPARKLE_SVG = `
<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <path d="M10 2 L11.2 6.8 L16 8 L11.2 9.2 L10 14 L8.8 9.2 L4 8 L8.8 6.8 Z" fill="currentColor"/>
  <path d="M16 13 L16.7 15.3 L19 16 L16.7 16.7 L16 19 L15.3 16.7 L13 16 L15.3 15.3 Z" fill="currentColor"/>
  <path d="M19 5 L19.5 6.5 L21 7 L19.5 7.5 L19 9 L18.5 7.5 L17 7 L18.5 6.5 Z" fill="currentColor"/>
  <path d="M12 11 L12.5 12.5 L14 13 L12.5 13.5 L12 15 L11.5 13.5 L10 13 L11.5 12.5 Z" fill="currentColor" opacity=".75"/>
</svg>`.trim();

  /* 左上箭头（小窗态：往左上扩大） */
  const EXPAND_SVG = `
<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <path d="M14 5 H19 V10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M19 5 L12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
  <path d="M10 19 H5 V14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M5 19 L12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
</svg>`.trim();

  /* 右下箭头（大窗态：缩回右下小窗） */
  const SHRINK_SVG = `
<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <path d="M10 5 H5 V10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M5 5 L12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
  <path d="M14 19 H19 V14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M19 19 L12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
</svg>`.trim();

  let _mounted = false;
  let _state = null;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }
  function fmtAgo(iso) {
    if (!iso) return '-';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso).slice(0, 10);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + ' 天前';
    return String(iso).slice(0, 10);
  }
  function fmtK(n) {
    n = Number(n) || 0;
    return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
  }

  window.mountAgentDrawer = function () {
    if (_mounted) return;
    _mounted = true;

    if (!document.querySelector('link[href*="agent-drawer.css"]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = CSS_HREF;
      document.head.appendChild(link);
    }

    const fab = document.createElement('button');
    fab.className = 'agent-fab';
    fab.setAttribute('aria-label', '打开原声分析 Agent');
    fab.setAttribute('aria-expanded', 'false');
    fab.innerHTML = SPARKLE_SVG;
    document.body.appendChild(fab);

    const drawer = document.createElement('div');
    drawer.className = 'agent-drawer';
    drawer.setAttribute('role', 'dialog');
    drawer.setAttribute('aria-label', '原声分析 Agent');
    drawer.innerHTML =
      '<div class="agent-drawer-head">' +
        '<span class="title">原声分析助手 <small>· Lynx</small></span>' +
        '<button class="btn-head" data-act="ref" title="把当前页面查询数据摘要注入下一轮对话上下文">📥 引用当前查询</button>' +
        '<button class="btn-head danger" data-act="ref-clear" title="清空已引用的数据" hidden>🗑 清除引用</button>' +
        '<button class="btn-icon" data-act="resize" title="扩大窗口"></button>' +
        '<button class="btn-icon" data-act="close" title="关闭">×</button>' +
      '</div>' +
      '<div class="agent-drawer-refbar" data-refbar hidden></div>' +
      '<div class="agent-drawer-columns">' +
        '<aside class="agent-drawer-side" data-side>' +
          '<div class="agent-drawer-side-head">' +
            '<button class="btn primary" data-act="new" style="width:100%">+ 新对话</button>' +
            '<button class="btn sm" data-act="export" style="width:100%;margin-top:8px">📥 导出我的对话</button>' +
          '</div>' +
          '<div class="agent-drawer-side-list" data-sidelist>加载中…</div>' +
        '</aside>' +
        '<div class="agent-drawer-maincol">' +
          '<div class="agent-drawer-body" data-body></div>' +
          '<div class="agent-drawer-input">' +
            '<textarea data-input rows="1" placeholder="问点什么…（Enter 发送，Shift+Enter 换行）"></textarea>' +
            '<div class="input-bar">' +
              '<span class="hint">例：「玩家痛点」「近 30 天情感趋势」</span>' +
              '<button class="btn-send" data-send>发送</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="agent-drawer-foot">匿名 UUID 仅用于保存你的对话历史，不含个人信息</div>';
    document.body.appendChild(drawer);

    _state = {
      fab, drawer,
      body: drawer.querySelector('[data-body]'),
      sideListEl: drawer.querySelector('[data-sidelist]'),
      refbar: drawer.querySelector('[data-refbar]'),
      input: drawer.querySelector('[data-input]'),
      sendBtn: drawer.querySelector('[data-send]'),
      btnResize: drawer.querySelector('[data-act="resize"]'),
      btnRef: drawer.querySelector('[data-act="ref"]'),
      btnRefClear: drawer.querySelector('[data-act="ref-clear"]'),
      sessionId: null,
      streaming: false,
      open: false,
      size: 'small',
      ref: null,   // {label, summary, page, time}
      closing: false,
    };

    fab.addEventListener('click', () => {
      // 2026-09-10 交互契约：无论什么状态，点悬浮球展开的都是小窗；开着（小/大）再点 = 关闭
      if (_state.open) { toggleDrawer(false); return; }
      _state.size = 'small';
      _applySize();
      toggleDrawer(true);
    });
    drawer.querySelector('[data-act="close"]').addEventListener('click', () => toggleDrawer(false));
    _state.btnResize.addEventListener('click', () => {
      _state.size = (_state.size === 'small') ? 'large' : 'small';
      _applySize();
      if (_state.size === 'large') { loadSideList(); requestAnimationFrame(() => _scrollBodyBottom()); }
    });
    _state.btnRef.addEventListener('click', refCurrentQuery);
    _state.btnRefClear.addEventListener('click', () => clearRef(true));
    drawer.querySelector('[data-act="new"]').addEventListener('click', startNewChat);
    drawer.querySelector('[data-act="export"]').addEventListener('click', async () => {
      try { await window.Agent.exportMarkdown(); }
      catch (e) { toast(e.message, true); }
    });
    _state.sendBtn.addEventListener('click', () => send());
    _state.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    _state.input.addEventListener('input', () => {
      _state.input.style.height = 'auto';
      _state.input.style.height = Math.min(_state.input.scrollHeight, 140) + 'px';
    });
  };

  /* ---- 窗口尺寸 / 动效 ---- */
  function _applySize() {
    _state.drawer.dataset.size = _state.size;
    _state.btnResize.innerHTML = (_state.size === 'small') ? EXPAND_SVG : SHRINK_SVG;
    _state.btnResize.title = (_state.size === 'small') ? '扩大窗口（含历史对话）' : '缩回小窗';
  }

  function toggleDrawer(force) {
    const open = typeof force === 'boolean' ? force : !_state.open;
    if (open === _state.open || _state.closing) return;
    _state.open = open;
    _state.fab.setAttribute('aria-expanded', String(open));
    if (open) {
      _state.drawer.classList.remove('closing');
      _state.drawer.classList.add('open');
      _state.input.focus();
      requestAnimationFrame(() => _scrollBodyBottom());
      if (!_state.body.children.length) renderWelcome();
      if (_state.size === 'large') loadSideList();
    } else {
      // 2026-09-10：渐灭动画——播完再真正隐藏（小/大窗通用）
      _state.closing = true;
      const d = _state.drawer;
      d.classList.add('closing');
      const onEnd = () => {
        d.removeEventListener('animationend', onEnd);
        d.classList.remove('open', 'closing');
        _state.closing = false;
      };
      d.addEventListener('animationend', onEnd);
      // 兜底：动画事件被吞时 300ms 后强制收尾
      setTimeout(() => { if (_state.closing) onEnd(); }, 320);
    }
  }

  /* ---- 引用当前查询 ---- */
  async function refCurrentQuery() {
    if (_state.streaming) return;
    const ctx = window.__pageAgentContext;
    if (!ctx || typeof ctx.build_summary !== 'function') {
      toast('未查询到数据（当前页面不支持引用）', true);
      return;
    }
    try {
      const summary = await ctx.build_summary();
      _state.ref = {
        label: ctx.label || ctx.page,
        page: ctx.page,
        summary: String(summary || '').slice(0, 2000),  // 硬上限
        time: new Date(),
      };
      _state.refbar.innerHTML =
        '<span class="ref-icon">📋</span>' +
        '<span class="ref-text">已引用：' + esc(_state.ref.label) +
          ' · ' + esc(_hhmm(_state.ref.time)) + '</span>' +
        '<button class="ref-clear" data-refclear title="清除数据引用">✕</button>';
      _state.refbar.hidden = false;
      _state.refbar.querySelector('[data-refclear]').addEventListener('click', () => clearRef(true));
      _state.btnRefClear.hidden = false;
      _state.btnRef.classList.add('active');
      toast('数据已引用：' + _state.ref.label);
    } catch (e) {
      toast('引用失败：' + e.message, true);
    }
  }
  function clearRef(notify) {
    _state.ref = null;
    _state.refbar.hidden = true;
    _state.refbar.innerHTML = '';
    _state.btnRefClear.hidden = true;
    _state.btnRef.classList.remove('active');
    if (notify) toast('已清除数据引用');
  }
  function _hhmm(d) {
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }

  /* ---- 历史对话列表（大窗左栏；2026-09-10 自 #/agent 一级页迁入） ---- */
  async function loadSideList() {
    try {
      const r = await window.Agent.Sessions.list({ limit: 30 });
      const sessions = (r && r.sessions) || [];
      if (!sessions.length) {
        _state.sideListEl.innerHTML = '<div class="empty-sm">还没有对话，发送第一条消息开始</div>';
        return;
      }
      let html = '';
      for (let i = 0; i < sessions.length; i++) {
        const s = sessions[i];
        const active = (s.id === _state.sessionId) ? ' active' : '';
        html += '<div class="drawer-side-item' + active + '" data-id="' + esc(s.id) + '">' +
          '<div class="drawer-side-title">' + esc(s.title || '未命名对话') + '</div>' +
          '<div class="drawer-side-meta">' +
            '<span>' + esc(fmtAgo(s.updated_at)) + '</span>' +
            '<span class="badge dim">' + esc(s.page || '-') + '</span>' +
          '</div>' +
          '<button class="side-del" data-del="' + esc(s.id) + '" title="删除">×</button>' +
        '</div>';
      }
      _state.sideListEl.innerHTML = html;
      _state.sideListEl.querySelectorAll('.drawer-side-item').forEach(el => {
        el.addEventListener('click', (e) => {
          if (e.target.closest('[data-del]')) return;
          openSession(el.dataset.id);
        });
      });
      _state.sideListEl.querySelectorAll('[data-del]').forEach(b => {
        b.addEventListener('click', async (e) => {
          e.stopPropagation();
          const sid = b.dataset.del;
          if (!confirm('删除该对话？')) return;
          try {
            await window.Agent.Sessions.del(sid);
            if (_state.sessionId === sid) {
              _state.sessionId = null;
              _state.body.innerHTML = '';
              renderWelcome();
            }
            await loadSideList();
            toast('已删除');
          } catch (err) { toast(err.message, true); }
        });
      });
    } catch (e) {
      _state.sideListEl.innerHTML = '<div class="empty-sm">加载失败：' + esc(e.message) + '</div>';
    }
  }

  async function openSession(sid) {
    if (_state.streaming) { toast('请等当前回复完成', true); return; }
    try {
      const detail = await window.Agent.Sessions.get(sid);
      _state.sessionId = sid;
      _state.body.innerHTML = '';
      (detail.messages || []).forEach(m => appendHistoryMsg(m));
      _scrollBodyBottom();
      loadSideList();
    } catch (e) { toast(e.message, true); }
  }

  function startNewChat() {
    if (_state.streaming) { toast('请等当前回复完成', true); return; }
    _state.sessionId = null;
    _state.body.innerHTML = '';
    renderWelcome();
    loadSideList();
  }

  /* ---- 欢迎消息 ---- */
  function renderWelcome() {
    appendMsg({ role: 'assistant', text:
      '你好，我是原声分析助手。可以问：\n' +
      '- 「玩家主要痛点是什么」\n' +
      '- 「最近 30 天的情感趋势」\n' +
      '- 「举几个负向评论的原声例子」\n' +
      '- 「黑神话战斗系统的看法」\n\n' +
      '提示：在看板页点「📥 引用当前查询」可把当前查询数据带入对话。' +
      '点右上角箭头可扩大窗口查看历史对话。',
    });
    _state.sessionId = null;
  }

  /* ---- 发送 ---- */
  async function send() {
    if (_state.streaming) return;
    const text = _state.input.value.trim();
    if (!text) return;
    _state.input.value = '';
    _state.input.style.height = 'auto';

    appendMsg({ role: 'user', text });

    if (!_state.sessionId) {
      const page = ((location.hash || '').replace(/^#\//, '') || 'dashboard').split('?')[0].split('/')[0];
      const ctx = window.__pageAgentContext;  // build_summary 是函数，JSON.stringify 自动忽略
      try {
        const sess = await window.Agent.Sessions.create({
          page, page_context: ctx ? JSON.stringify(ctx) : null,
          title: text.slice(0, 30),
        });
        _state.sessionId = sess.id;
        if (_state.size === 'large') loadSideList();
      } catch (e) {
        appendMsg({ role: 'assistant', error: '建会话失败：' + e.message });
        return;
      }
    }

    _state.streaming = true;
    _state.sendBtn.disabled = true;
    _state.btnRef.disabled = true;
    const assistantEl = appendMsg({ role: 'assistant', empty: true, dots: true });

    try {
      for await (const ev of window.Agent.chatStream({
        session_id: _state.sessionId, user_msg: text,
        context: _state.ref ? _state.ref.summary : null,  // 2026-09-10：引用数据随请求传，不落库
      })) {
        if (ev.event === 'token') {
          appendToken(assistantEl, ev.data.delta || '');
        } else if (ev.event === 'tool_start') {
          const t = assistantEl.querySelector('.typing');
          if (t) t.remove();
          appendToolCall(ev.data);
        } else if (ev.event === 'tool_end') {
          updateToolCallStatus(ev.data);
        } else if (ev.event === 'done') {
          const t = assistantEl.querySelector('.typing');
          if (t) t.remove();
          // 流式期间纯文本累积，done 后整体渲染 Markdown（一次性，避免逐 token 重排）
          assistantEl.classList.add('md');
          assistantEl.innerHTML = window.renderAgentMd(assistantEl.textContent);
          if (ev.data && ev.data.max_rounds_hit) {
            const rounds = ev.data.rounds || 5;
            appendNotice(
              `⚠️ 已达工具调用上限（${rounds} 轮）。如需完整回答，请把问题拆得更具体，或直接到看板看指标/筛选评论。`,
              'warn'
            );
          }
          if (ev.data && ev.data.usage) appendUsage(ev.data.usage);
        } else if (ev.event === 'error') {
          assistantEl.classList.add('error');
          assistantEl.textContent = '⚠ ' + (ev.data.message || '请求失败');
        }
      }
    } catch (e) {
      assistantEl.classList.add('error');
      assistantEl.textContent = '⚠ ' + e.message;
    } finally {
      _state.streaming = false;
      _state.sendBtn.disabled = false;
      _state.btnRef.disabled = false;
      _scrollBodyBottom();
    }
  }

  /* ---- 消息流渲染 ---- */
  function appendMsg(o) {
    const el = document.createElement('div');
    el.className = 'msg ' + o.role + (o.empty ? ' empty' : '') + (o.error ? ' error' : '');
    if (o.dots) {
      el.innerHTML = '<span class="typing-dot"></span>' +
        '<span class="typing-dot"></span>' +
        '<span class="typing-dot"></span>';
    } else if (o.error) {
      el.textContent = '⚠ ' + o.error;
    } else if (o.role === 'assistant' && window.renderAgentMd) {
      // assistant 消息渲染 Markdown（2026-09-10；DOMPurify 已净化）
      el.classList.add('md');
      el.innerHTML = window.renderAgentMd(o.text);
    } else {
      el.textContent = o.text || '';
    }
    _state.body.appendChild(el);
    _scrollBodyBottom();
    return el;
  }

  /* 历史会话消息回放（2026-09-10 自 #/agent 一级页迁入） */
  function appendHistoryMsg(m) {
    if (m.role === 'user') {
      appendMsg({ role: 'user', text: m.content || '' });
    } else if (m.role === 'assistant') {
      let calls = [];
      if (m.tool_calls) {
        try { calls = (typeof m.tool_calls === 'string') ? JSON.parse(m.tool_calls) : m.tool_calls; }
        catch (e) { calls = []; }
      }
      (calls || []).forEach(tc => {
        let argsObj = tc.arguments;
        if (typeof argsObj === 'string') {
          try { argsObj = JSON.parse(argsObj); } catch (e) { /* keep raw */ }
        }
        appendToolCall({ id: tc.id, name: tc.name, args: argsObj || {}, done: true });
      });
      if (m.content) appendMsg({ role: 'assistant', text: m.content });
    } else if (m.role === 'tool') {
      const el = _state.body.querySelector('.tool-call[data-id="' + esc(m.tool_call_id || '') + '"]');
      if (el) {
        el.dataset.status = 'done';
        const s = el.querySelector('.tool-status');
        s.className = 'tool-status done';
        s.textContent = '完成';
        const content = m.content || '';
        el.querySelector('.tool-call-body').textContent =
          content.slice(0, 300) + (content.length > 300 ? '...[truncated]' : '');
      }
    }
  }

  function appendNotice(text, kind) {
    const el = document.createElement('div');
    el.className = 'msg-system-notice ' + (kind || 'warn');
    el.textContent = text;
    _state.body.appendChild(el);
    _scrollBodyBottom();
  }

  /* 2026-09-10：本轮 token 消耗小字（usage 由后端多轮累加后经 done 事件返回） */
  function appendUsage(u) {
    const el = document.createElement('div');
    el.className = 'msg-usage';
    const cached = Number(u.cached_tokens) || 0;
    el.textContent = '⚡ 本轮 ' + fmtK(u.prompt_tokens) + ' in' +
      (cached ? '（缓存 ' + fmtK(cached) + '）' : '') +
      ' / ' + fmtK(u.completion_tokens) + ' out';
    _state.body.appendChild(el);
    _scrollBodyBottom();
  }

  function appendToken(el, delta) {
    if (el.classList.contains('empty')) {
      el.classList.remove('empty');
      el.textContent = '';
    }
    const t = el.querySelector('.typing');
    if (t) t.remove();
    el.textContent += delta;
    _scrollBodyBottom();
  }

  function appendToolCall(d) {
    const el = document.createElement('div');
    el.className = 'tool-call';
    el.dataset.id = d.id;
    el.dataset.status = d.done ? 'done' : 'running';
    el.innerHTML =
      '<div class="tool-call-head">' +
        '<span class="tool-name">⚙ ' + esc(d.name) + '</span>' +
        '<span class="tool-status ' + (d.done ? 'done' : 'running') + '">' + (d.done ? '完成' : '调用中…') + '</span>' +
      '</div>' +
      '<div class="tool-call-body">' + esc(JSON.stringify(d.args || {}, null, 2)) + '</div>';
    el.querySelector('.tool-call-head').addEventListener('click', () => {
      el.classList.toggle('expanded');
    });
    _state.body.appendChild(el);
    _scrollBodyBottom();
  }

  function updateToolCallStatus(d) {
    const el = _state.body.querySelector('.tool-call[data-id="' + d.id + '"]');
    if (!el) return;
    el.dataset.status = 'done';
    const s = el.querySelector('.tool-status');
    s.classList.remove('running');
    s.classList.add('done');
    s.textContent = '完成';
    el.querySelector('.tool-call-body').textContent = d.result_preview || '';
    _scrollBodyBottom();
  }

  function _scrollBodyBottom() {
    _state.body.scrollTop = _state.body.scrollHeight;
  }
})();
