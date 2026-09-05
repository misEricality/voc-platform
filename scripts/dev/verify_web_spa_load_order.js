// 临时验证脚本：模拟浏览器按 index.html 的顺序求值 JS，检查 Routes 注册表
// 用完即删（阶段 4 冒烟辅助，不属于正式代码）
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const WEB = path.join(__dirname, '..', '..', 'product', 'web', 'src');

// 最小 DOM stub（页面脚本顶层只做 addEventListener / 赋值）
globalThis.window = {
  addEventListener() {},
  location: { hash: '#/dashboard' },
};
globalThis.document = {
  getElementById: () => ({ addEventListener() {}, innerHTML: '', textContent: '' }),
  querySelectorAll: () => [],
  querySelector: () => null,
  addEventListener() {},
  removeEventListener() {},
};
globalThis.fetch = async () => ({
  ok: true, json: async () => ({ ok: true, data: [] }),
});
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '#66c0f4' });
globalThis.echarts = {};
globalThis.location = globalThis.window.location;
globalThis.confirm = () => false;

const order = ['api.js', 'charts.js', 'pages/dashboard.js', 'pages/compare.js',
  'pages/bilibili.js', 'pages/data.js', 'pages/admin.js', 'main.js'];

for (const f of order) {
  const code = fs.readFileSync(path.join(WEB, f), 'utf8');
  try {
    vm.runInThisContext(code, { filename: f });
    console.log(`  OK   ${f}`);
  } catch (e) {
    console.log(`  FAIL ${f} → ${e.constructor.name}: ${e.message}`);
    process.exit(1);
  }
}

// 等待 boot 的异步链（health → renderRoute → dashboard 页 fetch 全部 stub 为空数据）
setTimeout(() => {
  console.log('Routes keys:', Object.keys(Routes).join(', '));
  if (Object.keys(Routes).length === 5) {
    console.log('PASS: 5 个页面全部注册成功，求值顺序无 TDZ 问题');
    process.exit(0);
  } else {
    console.log('FAIL: 页面注册不完整');
    process.exit(1);
  }
}, 300);
