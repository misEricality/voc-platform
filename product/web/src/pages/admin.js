/* 系统管理：采集任务 CRUD（WEB_DASHBOARD.md §4.3 字段约束）
   Steam：新增 = URL/AppID（回填名称）；编辑可改 name/language/count；暂停 = enabled
   BiliBili：新增 = BV号/URL（自动识别 pubdate）；pause/resume/reidentify；fetched 禁删 */
Routes.admin = async function (app) {
  const status = await API.get('/api/auth/status');
  if (!status.logged_in) { renderLogin(app); return; }
  await renderAdmin(app);
};

/* ---------- 弹窗「查找」：URL/ID 即时查询目标标题与日期（不落库） ---------- */
function wireLookup(modal, platform, inputSel, infoSel, nameSel) {
  const btn = modal.querySelector('[data-lookup]');
  const input = modal.querySelector(inputSel);
  const info = modal.querySelector(infoSel);
  const nameInput = nameSel ? modal.querySelector(nameSel) : null;
  if (!btn || !input || !info) return;
  btn.addEventListener('click', async () => {
    const url = input.value.trim();
    if (!url) { info.textContent = '请先输入 URL / ID'; return; }
    btn.disabled = true; btn.textContent = '查找中…';
    try {
      const r = await API.get(`/api/admin/tasks/lookup?platform=${platform}&url_or_id=${encodeURIComponent(url)}`);
      if (platform === 'steam') {
        if (nameInput) nameInput.value = r.name;  // 自动填入名称
        info.textContent = `已识别：${r.name}` + (r.release_date ? ` · 发行日期 ${r.release_date}` : '');
      } else {
        info.textContent = `已识别：${r.title}` + (r.pubdate ? ` · 投稿 ${fmtDate(r.pubdate)}` : '');
      }
    } catch (e) { info.textContent = e.message; }
    btn.disabled = false; btn.textContent = '查找';
  });
}

/* ---------- 登录 ---------- */
function renderLogin(app) {
  app.innerHTML = `
    <div class="page-head"><h1>系统管理 - 采集任务</h1><span class="sub">管理员登录后可管理采集任务</span></div>
    <div class="card" style="max-width:380px;margin:40px auto">
      <h3>管理员登录</h3>
      <div class="field" style="margin-bottom:12px">
        <label style="display:block;font-size:12px;color:var(--muted);margin-bottom:5px">密码</label>
        <input type="password" id="pwd" style="width:100%" placeholder="ADMIN_PASSWORD_HASH 对应的明文密码">
      </div>
      <button class="btn primary" id="btnLogin" style="width:100%">登录</button>
      <div class="hint" style="font-size:11px;color:var(--dim);margin-top:10px">
        首次部署：python scripts/ops/hash_admin_password.py &lt;密码&gt; 生成哈希写入 .env
      </div>
    </div>`;
  const doLogin = async () => {
    try {
      await API.post('/api/auth/login', { password: document.getElementById('pwd').value });
      toast('登录成功');
      Routes.admin(app);
    } catch (e) { toast(e.message, true); }
  };
  document.getElementById('btnLogin').addEventListener('click', doLogin);
  document.getElementById('pwd').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });
}

/* ---------- 主界面 ---------- */
async function renderAdmin(app) {
  app.innerHTML = `
    <div class="page-head">
      <h1>系统管理 - 采集任务</h1>
      <span class="sub">修改实时生效（下次 cron / 首采触发时执行）</span>
      <span style="flex:1"></span>
      <button class="btn sm" id="btnLogout">退出登录</button>
    </div>
    <div class="tabs">
      <button id="tabSteam" class="active">Steam</button>
      <button id="tabBili">BiliBili</button>
    </div>
    <div class="card">
      <div class="toolbar" style="justify-content:flex-end">
        <button class="btn primary" id="btnAdd">＋ 新增任务</button>
      </div>
      <div style="overflow:auto"><table class="tbl" id="tblTasks"></table></div>
    </div>`;

  let platform = 'steam';
  const tbl = document.getElementById('tblTasks');

  document.getElementById('btnLogout').addEventListener('click', async () => {
    await API.post('/api/auth/logout');
    toast('已退出');
    Routes.admin(app);
  });
  document.getElementById('tabSteam').addEventListener('click', () => switchTab('steam'));
  document.getElementById('tabBili').addEventListener('click', () => switchTab('bilibili'));
  document.getElementById('btnAdd').addEventListener('click', () => platform === 'steam' ? steamAddModal() : biliAddModal());

  function switchTab(p) {
    platform = p;
    document.getElementById('tabSteam').classList.toggle('active', p === 'steam');
    document.getElementById('tabBili').classList.toggle('active', p === 'bilibili');
    load();
  }

  async function load() {
    const d = await API.get(`/api/admin/tasks?platform=${platform}`);
    if (platform === 'steam') renderSteam(d.steam || []); else renderBili(d.bilibili || []);
  }

  /* ----- Steam ----- */
  function renderSteam(rows) {
    tbl.innerHTML = `
      <thead><tr><th>游戏</th><th>AppID</th><th>URL</th><th>语言</th><th>采集上限</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${rows.length ? rows.map(t => `<tr data-id="${t.id}">
        <td style="font-weight:600">${esc(t.name || '(未命名)')}</td>
        <td>${esc(t.target_id)}</td>
        <td><a href="${esc(t.url)}" target="_blank" rel="noopener">打开</a></td>
        <td>${esc(t.language || '-')}</td>
        <td>${t.count ?? 'auto'}</td>
        <td><span class="badge ${STATUS_BADGE[t.status_display] || 'dim'}">${t.status_display}</span></td>
        <td style="white-space:nowrap">
          <button class="btn sm" data-act="edit">编辑</button>
          <button class="btn sm" data-act="pause">${t.enabled ? '暂停' : '恢复'}</button>
          <button class="btn sm danger" data-act="del">删除</button>
        </td></tr>`).join('') : '<tr><td colspan="7" class="empty">暂无 Steam 任务</td></tr>'}</tbody>`;

    tbl.querySelectorAll('[data-act]').forEach(btn => btn.addEventListener('click', async e => {
      const id = +e.target.closest('tr').dataset.id;
      const row = rows.find(r => r.id === id);
      try {
        if (btn.dataset.act === 'edit') steamEditModal(row);
        else if (btn.dataset.act === 'pause') {
          await API.patch(`/api/admin/tasks/steam/${id}`, { enabled: !row.enabled });
          toast(row.enabled ? '已暂停' : '已恢复'); load();
        } else if (btn.dataset.act === 'del') {
          if (!confirm(`确认删除「${row.name || row.target_id}」？历史数据保留，仅停止采集。`)) return;
          await API.del(`/api/admin/tasks/steam/${id}`);
          toast('已删除'); load();
        }
      } catch (err) { toast(err.message, true); }
    }));
  }

  function steamAddModal() {
    const m = openModal(`
      <h2>新增 Steam 采集任务</h2>
      <div class="field"><label>游戏商店 URL 或 AppID *</label>
        <div style="display:flex;gap:8px">
          <input type="text" id="fUrl" placeholder="https://store.steampowered.com/app/2358720/ 或 2358720">
          <button class="btn sm" data-lookup style="flex:none">查找</button>
        </div>
        <div class="hint" id="fLookupInfo">支持商店链接或纯数字 AppID；「查找」返回游戏名与发行日期并自动填入名称</div></div>
      <div class="field"><label>名称（可选，留空自动获取）</label><input type="text" id="fName"></div>
      <div class="field"><label>语言</label>
        <select id="fLang"><option value="schinese">简体中文</option><option value="tchinese">繁体中文</option><option value="english">英语</option></select></div>
      <div class="field"><label>单次采集上限</label>
        <input type="text" id="fCount" placeholder="留空 = auto（按时间窗耗尽）">
        <div class="hint">一般留空；填数字 = 每次增量最多采 N 条</div></div>
      <div class="field"><label class="checkbox"><input type="checkbox" id="fBackfill" checked>加入后立即执行首次采集（近 7 天）</label></div>
      <div class="actions"><button class="btn" data-close>取消</button><button class="btn primary" id="fSave">创建</button></div>`);
    m.querySelector('[data-close]').addEventListener('click', closeModal);
    m.querySelector('#fSave').addEventListener('click', async () => {
      try {
        const countRaw = m.querySelector('#fCount').value.trim();
        const r = await API.post('/api/admin/tasks/steam', {
          url_or_id: m.querySelector('#fUrl').value.trim(),
          name: m.querySelector('#fName').value.trim() || null,
          language: m.querySelector('#fLang').value,
          count: countRaw ? parseInt(countRaw, 10) : null,
          backfill_days: m.querySelector('#fBackfill').checked ? 7 : null,
        });
        closeModal();
        toast(r.backfill_started ? '已创建，首次采集已在后台启动' : '已创建');
        load();
      } catch (e) { toast(e.message, true); }
    });
    wireLookup(m, 'steam', '#fUrl', '#fLookupInfo', '#fName');
  }

  function steamEditModal(t) {
    const m = openModal(`
      <h2>编辑任务：${esc(t.name || t.target_id)}</h2>
      <div class="field"><label>游戏商店 URL / AppID（不可修改）</label>
        <div style="display:flex;gap:8px">
          <input type="text" readonly value="${esc(t.target_id)}">
          <button class="btn sm" data-lookup style="flex:none">查找</button>
        </div>
        <div class="hint" id="fLookupInfo">appid 是数据主键，改 = 换游戏；如需更换请删除后重新添加</div></div>
      <div class="field"><label>名称</label><input type="text" id="fName" value="${esc(t.name || '')}"></div>
      <div class="field"><label>语言</label>
        <select id="fLang">
          ${['schinese', 'tchinese', 'english'].map(l => `<option value="${l}" ${t.language === l ? 'selected' : ''}>${l}</option>`).join('')}
        </select></div>
      <div class="field"><label>单次采集上限（留空 = auto）</label>
        <input type="text" id="fCount" value="${t.count ?? ''}"></div>
      <div class="actions"><button class="btn" data-close>取消</button><button class="btn primary" id="fSave">保存</button></div>`);
    m.querySelector('[data-close]').addEventListener('click', closeModal);
    m.querySelector('#fSave').addEventListener('click', async () => {
      try {
        const countRaw = m.querySelector('#fCount').value.trim();
        await API.patch(`/api/admin/tasks/steam/${t.id}`, {
          name: m.querySelector('#fName').value.trim(),
          language: m.querySelector('#fLang').value,
          count: countRaw ? parseInt(countRaw, 10) : null,
        });
        closeModal(); toast('已保存'); load();
      } catch (e) { toast(e.message, true); }
    });
    wireLookup(m, 'steam', 'input[readonly]', '#fLookupInfo', '#fName');
  }

  /* ----- BiliBili ----- */
  function renderBili(rows) {
    tbl.innerHTML = `
      <thead><tr><th>视频标题</th><th>BV号</th><th>URL</th><th>投稿时间</th><th>采集时间</th><th>评论/弹幕</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${rows.length ? rows.map(t => `<tr data-id="${t.id}">
        <td style="font-weight:600;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(t.title || '')}">${esc(t.title || '(未识别)')}</td>
        <td>${esc(t.bv_id)}</td>
        <td><a href="${esc(t.url)}" target="_blank" rel="noopener">打开</a></td>
        <td>${fmtDate(t.pubdate)}</td>
        <td title="${t.fetched_at ? '采集于 ' + fmtDate(t.fetched_at) : ''}">${
          t.fetched_at && t.pubdate
            ? Math.max(0, Math.round((new Date(t.fetched_at) - new Date(t.pubdate)) / 86400000)) + ' 天'
            : '-'}</td>
        <td>${fmtNum(t.comment_count)} / ${fmtNum(t.danmaku_count)}</td>
        <td><span class="badge ${STATUS_BADGE[t.status_display] || 'dim'}" title="${esc(t.fail_reason || '')}">${t.status_display}</span></td>
        <td style="white-space:nowrap">
          ${t.status !== 'fetched' ? `<button class="btn sm" data-act="edit">编辑</button>` : ''}
          ${['pending', 'scheduled', 'fetching', 'failed'].includes(t.status) ? `<button class="btn sm" data-act="pause">${t.status === 'paused' ? '恢复' : '暂停'}</button>` : ''}
          ${t.status !== 'fetched' ? `<button class="btn sm danger" data-act="del">删除</button>` : ''}
        </td></tr>`).join('') : '<tr><td colspan="8" class="empty">暂无 B 站任务</td></tr>'}</tbody>`;

    tbl.querySelectorAll('[data-act]').forEach(btn => btn.addEventListener('click', async e => {
      const id = +e.target.closest('tr').dataset.id;
      const row = rows.find(r => r.id === id);
      try {
        if (btn.dataset.act === 'edit') biliEditModal(row);
        else if (btn.dataset.act === 'pause') {
          await API.patch(`/api/admin/tasks/bilibili/${id}`, { action: row.status === 'paused' ? 'resume' : 'pause' });
          toast(row.status === 'paused' ? '已恢复' : '已暂停'); load();
        } else if (btn.dataset.act === 'del') {
          if (!confirm(`确认删除 ${row.bv_id}？`)) return;
          await API.del(`/api/admin/tasks/bilibili/${id}`);
          toast('已删除'); load();
        }
      } catch (err) { toast(err.message, true); }
    }));
  }

  function biliAddModal() {
    const m = openModal(`
      <h2>新增 BiliBili 采集任务</h2>
      <div class="field"><label>视频 URL 或 BV 号 *</label>
        <div style="display:flex;gap:8px">
          <input type="text" id="fBv" placeholder="https://www.bilibili.com/video/BV1xxx 或 BV1xxx">
          <button class="btn sm" data-lookup style="flex:none">查找</button>
        </div>
        <div class="hint" id="fLookupInfo">提交后自动识别标题与投稿时间；投稿满 7 天后由每日 cron 触发采集</div></div>
      <div class="field"><label>备注（可选）</label><input type="text" id="fNote"></div>
      <div class="field"><label class="checkbox"><input type="checkbox" id="fNow">立即采集（跳过 7 天等待，适合已发布较久的视频）</label></div>
      <div class="actions"><button class="btn" data-close>取消</button><button class="btn primary" id="fSave">创建</button></div>`);
    m.querySelector('[data-close]').addEventListener('click', closeModal);
    m.querySelector('#fSave').addEventListener('click', async () => {
      try {
        const r = await API.post('/api/admin/tasks/bilibili', {
          url_or_id: m.querySelector('#fBv').value.trim(),
          note: m.querySelector('#fNote').value.trim() || null,
          backfill: m.querySelector('#fNow').checked,
        });
        closeModal();
        toast(r.backfill_started ? '已创建，立即采集中' : '已创建，等待投稿满 7 天后自动采集');
        load();
      } catch (e) { toast(e.message, true); }
    });
    wireLookup(m, 'bilibili', '#fBv', '#fLookupInfo', null);
  }

  function biliEditModal(t) {
    const m = openModal(`
      <h2>编辑任务：${esc(t.bv_id)}</h2>
      <div class="field"><label>BV号 / 标题 / 投稿时间（系统识别，不可修改）</label>
        <input type="text" readonly value="${esc(t.bv_id)} · ${esc(t.title || '未识别')} · ${fmtDate(t.pubdate)}">
        <div class="hint">如识别有误，保存后用列表外的「重新识别」流程（编辑弹窗保存后可再触发）</div></div>
      <div class="field"><label>备注</label><input type="text" id="fNote" value="${esc(t.note || '')}"></div>
      <div class="field"><label class="checkbox"><input type="checkbox" id="fReid">保存时重新识别投稿时间（B 站接口）</label></div>
      <div class="actions"><button class="btn" data-close>取消</button><button class="btn primary" id="fSave">保存</button></div>`);
    m.querySelector('[data-close]').addEventListener('click', closeModal);
    m.querySelector('#fSave').addEventListener('click', async () => {
      try {
        await API.patch(`/api/admin/tasks/bilibili/${t.id}`, { note: m.querySelector('#fNote').value.trim() });
        if (m.querySelector('#fReid').checked) {
          await API.patch(`/api/admin/tasks/bilibili/${t.id}`, { action: 'reidentify' });
        }
        closeModal(); toast('已保存'); load();
      } catch (e) { toast(e.message, true); }
    });
  }

  await load();
};
