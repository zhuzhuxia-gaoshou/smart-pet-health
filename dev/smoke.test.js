// P4 integration smoke test: jsdom renders the real frontend against the live API
const fs = require('fs');
const { JSDOM } = require('jsdom');

const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');
const BASE = 'http://127.0.0.1:8000';

const dom = new JSDOM(html, { url: BASE + '/app', pretendToBeVisual: true, runScripts: 'outside-only' });
const { window } = dom;
window.matchMedia = q => ({ matches: false, media: q, addListener(){}, removeListener(){}, addEventListener(){}, removeEventListener(){} });
window.fetch = (path, opts) => fetch(BASE + path, opts);
window.scrollTo = () => {};
const sleep = ms => new Promise(r => setTimeout(r, ms));

const results = [];
const check = (name, cond, extra) => results.push({ name, ok: !!cond, extra: cond ? '' : (extra || '') });

(async () => {
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  // expose script-scoped bindings for the test driver
  const epilogue = `
  ;window.__T = {
    getState: () => state,
    go, renderLibrary, renderDashboard, switchTab, api, refreshData, sendChat,
    toggleTheme, openDetail, $, renderMemories, renderMemTimeline,
  };`;
  window.eval(script + epilogue);
  await sleep(1500);
  const T = window.__T;
  const doc = window.document;
  const n = sel => doc.querySelectorAll(sel).length;
  const state = () => T.getState();

  check('boot loads pets (>=3)', state().pets.length >= 3, 'got ' + state().pets.length);
  check('stat cards = 3', n('#stat-row .stat-card') === 3);
  check('reminders rendered', n('#dash-reminders .reminder-item') >= 3, 'got ' + n('#dash-reminders .reminder-item'));
  check('activity rendered', n('#dash-activity .activity-item') >= 3);
  check('suggest chips', n('#suggest-chips .chip') === 3);
  check('stats are clickable', n('#stat-row .stat-card.clickable') === 3);
  check('dash reminders capped at 4', n('#dash-reminders .reminder-item') <= 4 && n('#dash-reminders .reminder-item') >= 3,
    'got ' + n('#dash-reminders .reminder-item'));
  check('activity capped at 5', n('#dash-activity .activity-item') <= 5);
  check('month overview removed', !T.$('dash-month-card'));
  check('mini pets removed', !T.$('dash-pets-mini'));
  T.go('library', { back: true });
  check('lib back link shown from stat card', T.$('lib-back').style.display !== 'none');
  T.go('library');
  check('lib back link hidden via tab', T.$('lib-back').style.display === 'none');

  // 统计卡点击 → 新页面
  T.go('records');
  await sleep(600);
  const recCount = (await (await fetch(BASE + '/api/records')).json()).records.length;
  check('records view lists all', n('#records-list .rec-row') === recCount, n('#records-list .rec-row') + ' vs ' + recCount);
  T.go('reminders');
  await sleep(200);
  check('reminders view lists all', n('#reminders-full-list .reminder-item') === state().reminders.length,
    n('#reminders-full-list .reminder-item') + ' vs ' + state().reminders.length);

  // 详情页"从哪来回哪去"
  T.go('reminders');
  await sleep(200);
  await T.go('detail', { petId: 2, from: 'reminders' });
  await sleep(600);
  check('back button says 返回到期提醒', (doc.querySelector('#view-detail .back-link') || {}).textContent?.includes('到期提醒'),
    (doc.querySelector('#view-detail .back-link') || {}).textContent);
  T.go('records');
  await T.go('detail', { petId: 1, from: 'records' });
  await sleep(600);
  check('back button says 返回健康记录', (doc.querySelector('#view-detail .back-link') || {}).textContent?.includes('健康记录'));
  T.go('dashboard');
  await T.go('detail', { petId: 1, from: 'dashboard' });
  await sleep(600);
  check('back button says 返回仪表盘', (doc.querySelector('#view-detail .back-link') || {}).textContent?.includes('仪表盘'));

  // ---------- 回忆集 ----------
  const tabTexts = [...doc.querySelectorAll('#nav-tabs .tab-btn')].map(b => b.textContent.trim()).join('|');
  check('nav order 宠物→回忆集→AI助手', tabTexts === '仪表盘|宠物|回忆集|AI 助手', tabTexts);
  T.go('memories');
  await sleep(700);
  const memAll = (await (await fetch(BASE + '/api/memories')).json()).memories.length;
  check('memories seeded (>=8)', memAll >= 8, 'got ' + memAll);
  check('mem timeline lists all', n('#mem-timeline .mem-item') === memAll, n('#mem-timeline .mem-item') + ' vs ' + memAll);
  check('year group headers', n('#mem-timeline .mem-year') >= 2);
  check('thumbnails present', n('#mem-timeline .mem-thumb') >= 5);
  check('pet chips = pets', n('#mem-pet-chips .mem-pet-chip') === state().pets.length);
  const keke = state().pets.find(p => p.name === '可乐');
  if (keke) {
    T.$('mem-filter').value = String(keke.id);
    T.renderMemTimeline();
    const kekeMems = (await (await fetch(BASE + '/api/memories?pet_id=' + keke.id)).json()).memories.length;
    check('mem filter by pet', n('#mem-timeline .mem-item') === kekeMems, n('#mem-timeline .mem-item') + ' vs ' + kekeMems);
    T.$('mem-filter').value = ''; T.renderMemTimeline();
    await T.go('memwall', { petId: keke.id });
    await sleep(600);
    check('wall shows 陪伴天数', (doc.querySelector('#view-memwall .wall-sum') || {}).textContent?.includes('天'));
    check('wall lists own items', n('#view-memwall .mem-item') === kekeMems);
    await T.api('/api/memories', { method: 'POST', body: JSON.stringify({ date: '2026-01-01', title: '__smoke_mem__', text: 'x', pet_id: keke.id }) });
    const mm = (await (await fetch(BASE + '/api/memories')).json()).memories.find(x => x.title === '__smoke_mem__');
    check('create memory via API', !!mm);
    if (mm) { await T.api('/api/memories/' + mm.id, { method: 'DELETE' }); }
    await T.renderMemories();
    check('memory deleted', ![...doc.querySelectorAll('#mem-timeline .mem-title')].some(e => e.textContent === '__smoke_mem__'));
  }
  T.go('dashboard');
  await sleep(300);

  T.go('library');
  const petTotal = state().pets.length;
  check('pet grid matches data', n('#pet-grid .pet-card') === petTotal, n('#pet-grid .pet-card') + ' vs ' + petTotal);
  T.$('lib-search').value = '柯基'; T.renderLibrary();
  check('search by breed', n('#pet-grid .pet-card') === 1, 'got ' + n('#pet-grid .pet-card'));
  T.$('lib-search').value = ''; T.$('lib-type').value = 'cat'; T.renderLibrary();
  check('filter cats', n('#pet-grid .pet-card') === 1, 'got ' + n('#pet-grid .pet-card'));
  T.$('lib-type').value = ''; T.renderLibrary();
  check('grid restored', n('#pet-grid .pet-card') === petTotal);

  await T.openDetail(1);
  await sleep(500);
  const tlCount = (await (await fetch(BASE + '/api/pets/1/records')).json()).records.length;
  check('timeline items', n('#pane-timeline .tl-item') === tlCount, n('#pane-timeline .tl-item') + ' vs ' + tlCount);
  T.switchTab('weight', doc.querySelector('[data-tab=weight]'));
  await sleep(200);
  check('weight svg dots', n('#weight-chart .wc-dot') >= 4, 'got ' + n('#weight-chart .wc-dot'));
  check('weight svg has line', n('#weight-chart svg path') >= 2);
  T.switchTab('props', doc.querySelector('[data-tab=props]'));
  check('props grid', n('#pane-props .prop-cell') >= 8);

  await T.api('/api/pets', { method: 'POST', body: JSON.stringify({ name: '__smoke_test__', type: 'cat', weight: 3.0, status: 'healthy' }) });
  await T.refreshData();
  const cid = (state().pets.find(p => p.name === '__smoke_test__') || {}).id;
  check('create pet visible', !!cid, 'id=' + cid);
  if (cid) {
    await T.api('/api/pets/' + cid, { method: 'DELETE' });
    await T.refreshData();
    check('delete pet cleanup', !state().pets.some(p => p.name === '__smoke_test__'));
  }

  T.go('chat');
  await sleep(800);
  check('chat welcome', n('#chat-scroll .msg.bot') >= 1);
  T.$('chat-input').value = '最近有哪些到期或逾期的事项？';
  await T.sendChat();
  await sleep(800);
  const last = [...doc.querySelectorAll('#chat-scroll .msg.bot .bubble')].pop();
  const txt = last ? last.textContent : '';
  check('AI answer mentions reminders', /临期|逾期/.test(txt), txt.slice(0, 60));
  check('mode tag shown', n('#chat-scroll .mode-tag') >= 1);
  // 供应商状态可能在线路中途翻转（如余额耗尽触发熔断），status=example 时容忍历史标签
  const expectMode = (await (await fetch(BASE + '/api/agent/status')).json()).mode;
  check('agent mode matches status',
    state().agentMode === expectMode || (expectMode === 'example' && ['agent', 'example'].includes(state().agentMode)),
    state().agentMode + ' vs ' + expectMode);
  check('copy button exists', n('#chat-scroll .msg-actions .btn') >= 1);

  T.toggleTheme();
  check('dark mode', doc.documentElement.dataset.theme === 'dark');
  T.toggleTheme();
  check('back to light', doc.documentElement.dataset.theme === 'light');
  check('theme persisted', window.localStorage.getItem('pet-theme') !== null);

  const fails = results.filter(r => !r.ok);
  for (const r of results) console.log((r.ok ? 'PASS ' : 'FAIL ') + r.name + (r.extra ? '  [' + r.extra + ']' : ''));
  console.log(`\n${results.length - fails.length}/${results.length} passed`);
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error('SMOKE CRASH:', e); process.exit(2); });
