/* ECharts 公共：token 颜色读取（不写死 hex，对齐 DESIGN_TOKENS §7）+ 实例 dispose 管理 */
const Charts = (() => {
  const instances = new Map();

  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function palette() {
    return {
      primary: token('--primary'), pos: token('--pos'), neu: token('--neu'),
      neg: token('--neg'), sky: token('--sky'), muted: token('--muted'),
      line: token('--line'), ink: token('--ink'),
    };
  }
  function baseOption() {
    const p = palette();
    return {
      color: [p.primary, p.pos, p.neg, p.neu, p.sky],
      textStyle: { fontFamily: token('--font-sans') || undefined },
      tooltip: { backgroundColor: token('--panel3'), borderColor: token('--line3'), textStyle: { color: p.ink } },
      legend: { textStyle: { color: p.muted } },
      grid: { left: 12, right: 16, top: 36, bottom: 8, containLabel: true },
    };
  }

  /* 渲染/复用实例：旧 chart dispose 防泄漏（对齐 v0.3 模式） */
  function render(containerId, option) {
    const el = document.getElementById(containerId);
    if (!el) return;
    let chart = instances.get(containerId);
    if (chart && chart.getDom() !== el) { chart.dispose(); chart = null; }
    if (!chart) {
      chart = echarts.init(el);
      instances.set(containerId, chart);
    }
    chart.setOption({ ...baseOption(), ...option }, true);
    chart.resize();
  }

  function disposeAll() {
    instances.forEach(c => c.dispose());
    instances.clear();
  }
  window.addEventListener('resize', () => instances.forEach(c => c.resize()));

  function get(containerId) { return instances.get(containerId) || null; }

  return { render, disposeAll, palette, token, get };
})();
