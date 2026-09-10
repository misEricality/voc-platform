/* 匿名用户 ID（2026-09-09 决策 #16）
   - 首次调用生成 UUID v4 写入 localStorage
   - 后续稳定返回（清缓存才会重置）
   - 不是 PII，未来可平滑升级登录态

   所有需要按"我的对话"隔离的接口都通过 getAnonUserId() 注入 X-Anon-User-Id header。 */
(function () {
  const ANON_KEY = 'lynx.anon_user_id';

  function _genUuid() {
    if (window.crypto && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    const b = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const h = [];
    for (let i = 0; i < b.length; i++) h.push(b[i].toString(16).padStart(2, '0'));
    const hex = h.join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  window.getAnonUserId = function () {
    let id = localStorage.getItem(ANON_KEY);
    if (!id) {
      id = _genUuid();
      try { localStorage.setItem(ANON_KEY, id); } catch (e) { /* 隐私模式可能 quota 为 0 */ }
    }
    return id;
  };

  window.resetAnonUserId = function () { localStorage.removeItem(ANON_KEY); };
})();
