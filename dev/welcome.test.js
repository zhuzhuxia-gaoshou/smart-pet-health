// 欢迎页冒烟测试：加载无报错 · 主题键与应用统一(pet-theme) · 开始按钮触发过渡动画 · APP_URL 指向 /app
const fs = require('fs');
const { JSDOM, VirtualConsole } = require('jsdom');

// 与 smoke.test.js 相同约定：从装有 jsdom 的目录里以绝对路径读取仓库文件运行
const html = fs.readFileSync('C:/Users/Administrator/Desktop/项目/smart-pet-health/web/welcome.html', 'utf8');
const results = [];
const check = (name, cond, extra) => results.push({ name, ok: !!cond, extra: cond ? '' : (extra || '') });

const vc = new VirtualConsole();
const allErrors = [];
vc.on('jsdomError', e => allErrors.push(String(e)));
// jsdom 没有 IntersectionObserver（真实浏览器全支持）；注入直通 shim 让滚动淡入与后续脚本正常执行
const dom = new JSDOM(html, {
  url: 'http://127.0.0.1:8000/', runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.IntersectionObserver = class {
      constructor(cb) { this.cb = cb; }
      observe(t) { this.cb([{ isIntersecting: true, target: t }]); }
      unobserve() {} disconnect() {}
    };
  },
});
const realErrors = () => allErrors.filter(e => !/Not implemented/.test(e));
const { window } = dom;
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  await sleep(400);
  const doc = window.document;
  check('脚本无报错', realErrors().length === 0, realErrors()[0]);
  check('APP_URL 已指向 /app', html.includes("const APP_URL = '/app'"));
  check('主题键与应用统一为 pet-theme', html.includes("getItem('pet-theme')") && !html.includes("getItem('theme')"));

  const toggle = doc.getElementById('themeToggle');
  toggle.click();
  const theme = doc.documentElement.dataset.theme;
  check('亮暗切换生效', theme === 'dark' || theme === 'light', theme);
  check('偏好写入 pet-theme', window.localStorage.getItem('pet-theme') !== null,
    'storage=' + window.localStorage.getItem('pet-theme'));
  toggle.click();
  check('再次切换可逆', doc.documentElement.dataset.theme !== theme);

  const gate = doc.getElementById('gate');
  check('过渡层初始隐藏', gate && !gate.classList.contains('show'));
  doc.getElementById('startBtn').click();
  await sleep(200);
  check('点开始后过渡层出现', gate.classList.contains('show'));
  await sleep(100);
  check('过渡标题文案', doc.querySelector('.gate-title')?.textContent.includes('毛孩子'),
    doc.querySelector('.gate-title')?.textContent);
  // jsdom 不执行真实跳转，但赋值 window.location.href 会抛 "Not implemented: navigation" —— 以此确认跳转被触发
  await sleep(3600);
  const navErr = allErrors.find(e => /Not implemented: navigation/i.test(e));
  check('动画播完触发跳转(href=/app)', !!navErr, navErr || '无导航事件');

  const fails = results.filter(r => !r.ok);
  for (const r of results) console.log((r.ok ? 'PASS ' : 'FAIL ') + r.name + (r.extra ? '  [' + r.extra + ']' : ''));
  console.log(`\n${results.length - fails.length}/${results.length} passed`);
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error('CRASH:', e); process.exit(2); });
