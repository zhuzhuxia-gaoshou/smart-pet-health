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
  check('reminder rows have 完成 button', n('#reminders-full-list .rem-done') === state().reminders.length);

  // 提醒重复规则：monthly 记录到期 2027-01-31 → 完成 → 下一轮钳制到 2027-02-28
  {
    const created = await T.api('/api/pets/1/records', { method: 'POST',
      body: JSON.stringify({ type: 'deworm', title: '__smoke_repeat__', next_date: '2027-01-31', repeat_rule: 'monthly' }) });
    check('repeat record created', !!created.record && created.record.repeat_label === '每月');
    if (created.record) {
      const done = await T.api('/api/records/' + created.record.id + '/complete', { method: 'POST' });
      check('complete clears next_date', !!done.record && done.record.next_date === null);
      check('complete rolls next round (month-end clamp)', !!done.next && done.next.next_date === '2027-02-28', done.next && done.next.next_date);
      check('next round inherits rule', !!done.next && done.next.repeat_rule === 'monthly');
      // 二次完成同一条：已无到期日，不应再生成
      const again = await T.api('/api/records/' + created.record.id + '/complete', { method: 'POST' });
      check('complete is idempotent', again.next === null);
      await T.api('/api/records/' + created.record.id, { method: 'DELETE' });
      if (done.next) await T.api('/api/records/' + done.next.id, { method: 'DELETE' });
    }
    // 逾期完成：到期日在过去 → 下一轮从今天起算（weekly = 今天+7）
    const late = await T.api('/api/pets/1/records', { method: 'POST',
      body: JSON.stringify({ type: 'deworm', title: '__smoke_late__', next_date: '2020-01-01', repeat_rule: 'weekly' }) });
    if (late.record) {
      const d2 = await T.api('/api/records/' + late.record.id + '/complete', { method: 'POST' });
      const exp = new Date(Date.now() + 7 * 864e5), pad = x => String(x).padStart(2, '0');
      const expStr = `${exp.getFullYear()}-${pad(exp.getMonth() + 1)}-${pad(exp.getDate())}`;
      check('overdue completion rolls from today', !!d2.next && d2.next.next_date === expStr, (d2.next && d2.next.next_date) + ' vs ' + expStr);
      await T.api('/api/records/' + late.record.id, { method: 'DELETE' });
      if (d2.next) await T.api('/api/records/' + d2.next.id, { method: 'DELETE' });
    }
    const bad = await (await fetch(BASE + '/api/pets/1/records', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'deworm', title: 'x', repeat_rule: 'hourly' }) })).json();
    check('invalid repeat rule rejected', !!bad.error);
    const badDate = await (await fetch(BASE + '/api/pets/1/records', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'deworm', title: 'x', next_date: '2026/13/45' }) })).json();
    check('invalid date rejected with zh message', !!badDate.error && badDate.error.includes('YYYY-MM-DD'));
    await T.refreshData();
  }

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
  check('nav order 宠物→记账→日历→回忆集→AI助手', tabTexts === '仪表盘|宠物|记账|日历|回忆集|AI 助手', tabTexts);
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
  check('timeline 完成 buttons for dated items', n('#pane-timeline .tl-done') >= 1, 'got ' + n('#pane-timeline .tl-done'));
  T.switchTab('weight', doc.querySelector('[data-tab=weight]'));
  await sleep(200);
  check('weight svg dots', n('#weight-chart .wc-dot') >= 4, 'got ' + n('#weight-chart .wc-dot'));
  check('weight svg has line', n('#weight-chart svg path') >= 2);
  check('weight range pills', n('#weight-chart .wc-pill') === 4, 'got ' + n('#weight-chart .wc-pill'));
  check('weight stats row', n('#weight-chart .wc-stat') === 4);
  window.setWeightRange('90');
  await sleep(100);
  check('range 90d becomes active', doc.querySelectorAll('#weight-chart .wc-pill')[2].classList.contains('active'));
  check('range 90d filters dots', n('#weight-chart .wc-dot') < 5 || n('#weight-chart .empty-hint') === 1, 'got ' + n('#weight-chart .wc-dot'));
  window.setWeightRange('all');
  await sleep(100);
  check('range back to all', n('#weight-chart .wc-dot') >= 4, 'got ' + n('#weight-chart .wc-dot'));
  T.switchTab('props', doc.querySelector('[data-tab=props]'));
  check('props grid', n('#pane-props .prop-cell') >= 8);

  // 用药方案：API 创建 → 页签渲染 → 结束 → 删除
  {
    const created = await T.api('/api/pets/1/medications', { method: 'POST',
      body: JSON.stringify({ name: '__smoke_med__', dosage: '1 片', frequency: '每日一次', end_date: '2099-01-01' }) });
    check('medication created', !!created.medication && created.medication.status_label === '在用');
    await T.openDetail(1);
    await sleep(400);
    T.switchTab('meds', doc.querySelector('[data-tab=meds]'));
    check('meds pane renders items', n('#pane-meds .med-item') >= 1, 'got ' + n('#pane-meds .med-item'));
    check('meds pane shows new med', [...doc.querySelectorAll('#pane-meds .med-name')].some(e => e.textContent === '__smoke_med__'));
    check('meds tab label counts active', ((doc.querySelector('[data-tab=meds]') || {}).textContent || '').includes('用药 ·'));
    check('meds progress bar', n('#pane-meds .med-bar') >= 1);
    const fin = await T.api('/api/medications/' + created.medication.id, { method: 'PUT', body: JSON.stringify({ name: '__smoke_med__', status: 'finished' }) });
    check('medication finished', !!fin.medication && fin.medication.status === 'finished');
    await T.api('/api/medications/' + created.medication.id, { method: 'DELETE' });
    const after = await T.api('/api/pets/1/medications');
    check('medication deleted', !after.medications.some(m => m.name === '__smoke_med__'));
  }

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

  // 微交互体系：toast 三态 + 弹窗关闭动画类
  window.toast('smoke-warn', 'warn');
  check('toast warn level', n('#toast-root .toast.warn') === 1);
  window.toast('smoke-err', true);
  check('toast err compat', n('#toast-root .toast.err') === 1);
  window.openModal('<p>smoke</p>');
  check('modal opened', doc.getElementById('modal-overlay').classList.contains('open'));
  window.closeModal();
  check('modal closing anim', doc.getElementById('modal-overlay').classList.contains('closing'));
  await sleep(220);
  check('modal closed', !doc.getElementById('modal-overlay').classList.contains('open'));

  // 无障碍补丁：skip link / aria-live / tab 语义 / 弹窗 dialog+label 关联 / 可点行键盘可达
  check('skip link injected', !!doc.querySelector('.skip-link'));
  check('toast root aria-live', doc.getElementById('toast-root').getAttribute('aria-live') === 'polite');
  check('nav tabs have tab role', n('#nav-tabs [role=tab]') === 6);
  window.openModal('<h3>t</h3><div class="form-field"><label>名字</label><input name="x"></div>');
  await sleep(80);
  check('modal has dialog role', doc.getElementById('modal-box').getAttribute('role') === 'dialog');
  check('modal label auto-linked', !!doc.querySelector('#modal-box label[for]') && !!doc.querySelector('#modal-box input[id]'));
  window.closeModal(); await sleep(220);
  T.go('reminders'); await sleep(200);
  check('reminder rows keyboard reachable', n('#reminders-full-list .reminder-item') > 0 &&
    [...doc.querySelectorAll('#reminders-full-list .reminder-item')].every(el => el.getAttribute('role') === 'button' && el.getAttribute('tabindex') === '0'));

  // ---------- 记账：聚合精度 / 校验 / 页面渲染 / 编辑与清空宠物 / 空态 / 清理 ----------
  const kel = state().pets.find(p => p.name === '可乐');
  check('可乐 exists for expense tests', !!kel);
  if (kel) {
    const today = new Date(), yy = today.getFullYear(), mm = today.getMonth() + 1;
    const ym = `${yy}-${String(mm).padStart(2, '0')}`;
    const fetchSum = async () => (await (await fetch(BASE + `/api/expenses?year=${yy}&month=${mm}`)).json()).summary;
    const base = await fetchSum();
    const e1 = await T.api('/api/expenses', { method: 'POST', body: JSON.stringify({ pet_id: kel.id, category: 'medical', amount: 12.34, note: '__smoke_exp1__' }) });
    const e2 = await T.api('/api/expenses', { method: 'POST', body: JSON.stringify({ category: 'other', amount: 0.66, note: '__smoke_exp2__' }) });
    check('expense created with labels', e1.expense?.pet_name === '可乐' && e1.expense.category_label === '医疗' && e2.expense?.pet_id === null);
    const agg = await fetchSum();
    check('expense summary adds up to cents', Math.round((agg.total - base.total) * 100) === 1300 && agg.count === base.count + 2, `${base.total}→${agg.total}`);
    check('expense by_category tracks', Math.round(((agg.by_category.medical || 0) - (base.by_category.medical || 0)) * 100) === 1234);
    check('家庭共同 appears in by_pet', agg.by_pet.some(p => p.pet_id === null && p.pet_name === '家庭共同'));
    let err = '';
    try { await T.api('/api/expenses', { method: 'POST', body: JSON.stringify({ category: 'food', amount: -1 }) }); } catch (e) { err = e.message; }
    check('negative amount rejected in Chinese', err.includes('金额'), err);
    err = '';
    try { await T.api('/api/expenses', { method: 'POST', body: JSON.stringify({ category: 'food', amount: 1.005 }) }); } catch (e) { err = e.message; }
    check('3-decimal amount rejected', err.includes('两位小数'), err);

    T.go('ledger'); await sleep(700);
    check('ledger month label', (T.$('ledger-month').textContent || '').includes(`${mm}月`), T.$('ledger-month').textContent);
    check('ledger total card', (doc.querySelector('#ledger-total .exp-total') || {}).textContent?.includes('¥'));
    check('ledger chart 5 bars', n('#exp-chart svg .exp-bar-row') === 5, 'got ' + n('#exp-chart svg .exp-bar-row'));
    check('ledger rows = month rows', n('#ledger-list .exp-row') === agg.count, n('#ledger-list .exp-row') + ' vs ' + agg.count);
    check('ledger has 家庭共同 group', [...doc.querySelectorAll('#ledger-list .exp-group-head')].some(h => h.textContent.includes('家庭共同')));
    check('ledger rows keyboard reachable', [...doc.querySelectorAll('#ledger-list .exp-row')].every(el => el.getAttribute('role') === 'button' && el.getAttribute('tabindex') === '0'));
    window.openExpenseModal(e1.expense.id); await sleep(80);
    check('expense modal prefilled', doc.querySelector('#exp-form [name=amount]')?.value === '12.34' && doc.querySelector('#exp-form [name=pet_id]')?.value === String(kel.id));
    window.closeModal(); await sleep(220);
    const upd = await T.api('/api/expenses/' + e1.expense.id, { method: 'PUT', body: JSON.stringify({ pet_id: 0, category: 'medical', amount: 20 }) });
    check('PUT pet_id=0 → 家庭共同 & note kept', upd.expense.pet_id === null && upd.expense.amount === 20 && upd.expense.note === '__smoke_exp1__');
    state().ledgerYM = '2000-01'; await window.renderLedger(); await sleep(50);
    check('ledger empty state on empty month', !!doc.querySelector('#ledger-list .empty-state') && T.$('ledger-today').style.display !== 'none');
    window.ledgerToday(); await sleep(400);
    check('ledger back to current month', state().ledgerYM === ym && T.$('ledger-today').style.display === 'none');
    await T.api('/api/expenses/' + e1.expense.id, { method: 'DELETE' });
    await T.api('/api/expenses/' + e2.expense.id, { method: 'DELETE' });
    let gone = ''; try { await T.api('/api/expenses/' + e1.expense.id, { method: 'DELETE' }); } catch (e) { gone = e.message; }
    check('expense delete idempotent error', gone.includes('不存在'));
  }

  // ---------- 健康日历：三源聚合 / 栅格 / 今天格 / 当日卡 / 月切换 ----------
  if (kel) {
    const today = new Date(), yy = today.getFullYear(), mm = today.getMonth() + 1, dd = today.getDate();
    const todayIso = `${yy}-${String(mm).padStart(2, '0')}-${String(dd).padStart(2, '0')}`;
    // 自造数据，不依赖种子：一条今天到期的提醒（soon）+ 一条从今天起的长期用药（紫点）
    const rec = await T.api(`/api/pets/${kel.id}/records`, { method: 'POST', body: JSON.stringify({ type: 'checkup', title: '__smoke_cal__', next_date: todayIso }) });
    const med = await T.api(`/api/pets/${kel.id}/medications`, { method: 'POST', body: JSON.stringify({ name: '__smoke_med__', start_date: todayIso }) });
    try {
      check('calendar rejects month 13', (await fetch(BASE + '/api/calendar?year=2026&month=13')).status === 422);
      const cal = await (await fetch(BASE + `/api/calendar?year=${yy}&month=${mm}`)).json();
      check('calendar payload shape', cal.year === yy && cal.month === mm && typeof cal.days === 'object' && 'reminders' in cal.summary);
      const td = cal.days[todayIso] || { reminders: [], records: [], meds: [] };
      check('today bucket has soon reminder', td.reminders.some(r => r.title === '__smoke_cal__' && r.level === 'soon' && r.days_left === 0));
      check('today bucket has med + reminders carry level', td.meds.some(m => m.name === '__smoke_med__')
        && Object.values(cal.days).flatMap(d => d.reminders).every(r => ['overdue', 'soon', 'todo'].includes(r.level)));
      const daysLeftInMonth = new Date(yy, mm, 0).getDate() - dd + 1;
      check('open-ended med expands to month end', cal.summary.med_days >= daysLeftInMonth, `med_days ${cal.summary.med_days} < ${daysLeftInMonth}`);

      T.go('calendar'); await sleep(700);
      check('calendar month label', (T.$('cal-month').textContent || '').includes(`${mm}月`), T.$('cal-month').textContent);
      check('calendar 7 weekday headers', n('#cal-grid .cal-dow') === 7);
      const cells = n('#cal-grid .cal-cell');
      check('calendar grid is whole weeks (28~42)', cells % 7 === 0 && cells >= 28 && cells <= 42, 'got ' + cells);
      const todayCell = doc.querySelector('#cal-grid .cal-cell.today');
      check('calendar today cell highlighted', !!todayCell && todayCell.querySelector('.cal-num').textContent === String(dd));
      check('today cell shows dots (soon + med)', !!todayCell && todayCell.querySelectorAll('.cal-dot').length >= 2, 'got ' + (todayCell ? todayCell.querySelectorAll('.cal-dot').length : 0));
      check('active cells keyboard reachable', [...doc.querySelectorAll('#cal-grid .cal-cell:not(.dim)')].every(el => el.getAttribute('role') === 'button' && el.getAttribute('tabindex') === '0' && el.getAttribute('aria-label').includes('月')));
      check('dim cells not focusable', [...doc.querySelectorAll('#cal-grid .cal-cell.dim')].every(el => !el.hasAttribute('tabindex')));
      check('today cell aria says 今天', !!todayCell && (todayCell.getAttribute('aria-label') || '').includes('今天'));
      // jsdom(outside-only) 不执行内联 onclick，这里直接调用格子绑定的函数
      window.openDayCard(todayIso); await sleep(80);
      check('day card opens with today items', doc.getElementById('modal-overlay').classList.contains('open')
        && doc.querySelector('#modal-box h3')?.textContent.includes(`${mm}月${dd}日`)
        && [...doc.querySelectorAll('#modal-box .reminder-item .rem-title')].some(el => el.textContent.includes('__smoke_cal__'))
        && [...doc.querySelectorAll('#modal-box .med-done-item b')].some(el => el.textContent === '__smoke_med__'));
      check('day card 完成 button present', n('#modal-box .rem-done') >= 1);
      check('day card sets dayCardISO', state().dayCardISO === todayIso);
      window.closeModal(); await sleep(220);
      check('closeModal clears dayCardISO', state().dayCardISO === null);
      window.calShift(-1); await sleep(500);
      check('calendar shifts to previous month', (T.$('cal-month').textContent || '') !== `${yy}年${mm}月` && T.$('cal-today').style.display !== 'none');
      window.calToday(); await sleep(500);
      check('calendar back to today', (T.$('cal-month').textContent || '') === `${yy}年${mm}月` && T.$('cal-today').style.display === 'none');
    } finally {
      if (rec.record) await T.api('/api/records/' + rec.record.id, { method: 'DELETE' }).catch(() => {});
      if (med.medication) await T.api('/api/medications/' + med.medication.id, { method: 'DELETE' }).catch(() => {});
    }
  }

  const fails = results.filter(r => !r.ok);
  for (const r of results) console.log((r.ok ? 'PASS ' : 'FAIL ') + r.name + (r.extra ? '  [' + r.extra + ']' : ''));
  console.log(`\n${results.length - fails.length}/${results.length} passed`);
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error('SMOKE CRASH:', e); process.exit(2); });
