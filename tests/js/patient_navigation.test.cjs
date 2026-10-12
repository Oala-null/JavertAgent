// 使用真实 app.js 的点击入口，断言患者切换期间旧详情一直可见。
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const SOURCE = fs.readFileSync(path.join(__dirname, '../../src/javert/web/static/app.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function element(attributes = {}) {
  const attrs = { ...attributes };
  const classes = new Set((attrs.class || '').split(/\s+/).filter(Boolean));
  return {
    attributes: attrs, dataset: {}, style: {}, isConnected: true, innerHTML: '',
    childNodes: [], children: [], tagName: 'DIV',
    classList: {
      add(...names) { names.forEach(name => classes.add(name)); },
      remove(...names) { names.forEach(name => classes.delete(name)); },
      contains(name) { return classes.has(name); },
      toggle(name, force) {
        const enabled = force === undefined ? !classes.has(name) : force;
        if (enabled) classes.add(name); else classes.delete(name);
        return enabled;
      },
    },
    getAttribute(name) { return Object.hasOwn(attrs, name) ? attrs[name] : null; },
    setAttribute(name, value) { attrs[name] = String(value); },
    removeAttribute(name) { delete attrs[name]; },
    hasAttribute(name) { return Object.hasOwn(attrs, name); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    appendChild(child) { this.children.push(child); this.childNodes.push(child); child.parentNode = this; return child; },
    replaceChildren(...children) { this.children = []; this.childNodes = []; children.forEach(child => this.appendChild(child)); },
    contains(node) { return this === node; },
    closest() { return null; },
    focus() {}, scrollIntoView() {}, remove() { this.isConnected = false; },
  };
}

function page() {
  const pending = [], pushes = [], docEvents = {}, windowEvents = {};
  const state = { detail: null, replacements: 0, fullNavigations: 0, elements: new Map() };
  const location = new URL('https://example.test/workbench/CASE-SYNTHETIC-A?filter=v_and_i');
  location.assign = () => { state.fullNavigations += 1; };
  location.reload = () => { state.fullNavigations += 1; };
  const body = element({ class: 'theme-blue' });
  const modalRoot = element();
  const filterSelect = element(); filterSelect.value = 'v_and_i';
  const filterForm = element({ action: location.pathname });
  const sidebar = element({ id: 'sidebar', 'data-filter': 'v_and_i' });
  sidebar.querySelector = selector => selector.includes('select') ? filterSelect : null;
  const cards = ['A', 'B', 'C'].map(suffix => {
    const patient = `CASE-SYNTHETIC-${suffix}`;
    const card = element({ class: `patient-card${suffix === 'A' ? ' active' : ''}`,
      'data-patient-id': patient,
      href: `/workbench/${patient}?filter=v_and_i` });
    card.tagName = 'A';
    card.href = new URL(card.getAttribute('href'), location).href;
    card.closest = selector => selector.includes('patient-card') ? card : (selector.includes('sidebar') ? sidebar : null);
    return card;
  });
  function detail(patient, label = patient, filter = 'v_and_i') {
    const node = element({ class: 'detail-panel', id: 'detail-panel',
      'data-patient-id': patient, 'data-display-label': label, 'data-filter': filter });
    node.dataset = { patientId: patient, displayLabel: label, filter };
    node.innerHTML = `详情 ${label}`;
    node.replaceWith = replacement => {
      node.isConnected = false;
      replacement.isConnected = true;
      state.detail = replacement;
      state.replacements += 1;
    };
    return node;
  }
  state.detail = detail('CASE-SYNTHETIC-A');
  const document = {
    readyState: 'loading', title: 'CASE-SYNTHETIC-A · 工作台', body,
    head: { insertAdjacentHTML() {} }, activeElement: null,
    addEventListener(name, handler) { (docEvents[name] ||= []).push(handler); },
    getElementById(id) {
      if (state.elements.has(id)) return state.elements.get(id);
      if (id === 'detail-panel') return state.detail;
      if (id === 'sidebar') return sidebar;
      if (id === 'modal-root') return modalRoot;
      return body.children.find(child => child.id === id) || null;
    },
    querySelector(selector) {
      if (selector === '.detail-panel' || selector === '#detail-panel') return state.detail;
      if (selector.includes('.filter-form') && selector.includes('select')) return filterSelect;
      if (selector === '.filter-form') return filterForm;
      if (selector === '#sidebar' || selector === '.sidebar-panel') return sidebar;
      return null;
    },
    querySelectorAll(selector) { return selector.includes('.patient-card') ? cards : []; },
    createElement() { return element(); },
    importNode(node) { return node; },
    contains(node) { return node.isConnected; },
  };
  const context = {
    document, location, URL, URLSearchParams, AbortController,
    JAVERT_PATIENT: 'CASE-SYNTHETIC-A', JAVERT_DISPLAY_LABEL: 'CASE-SYNTHETIC-A',
    history: {
      pushState(data, title, href) { pushes.push({ data, title, href }); location.href = new URL(href, location).href; },
      replaceState(data, title, href) { if (href) location.href = new URL(href, location).href; },
    },
    DOMParser: class {
      parseFromString(html) {
        const attrs = Object.fromEntries([...html.matchAll(/(data-[\w-]+)="([^"]*)"/g)].map(match => [match[1], match[2]]));
        const panel = attrs['data-patient-id'] ? detail(attrs['data-patient-id'], attrs['data-display-label'], attrs['data-filter']) : null;
        return { title: (html.match(/<title>(.*?)<\/title>/) || [])[1] || '',
          querySelector(selector) { return selector === '.detail-panel' || selector === '#detail-panel' ? panel : null; } };
      }
    },
    fetch(url, options) { return new Promise((resolve, reject) => pending.push({ url, options, resolve, reject })); },
    addEventListener(name, handler) { (windowEvents[name] ||= []).push(handler); },
    setTimeout() {}, clearTimeout() {}, setInterval() {}, clearInterval() {},
    requestAnimationFrame(callback) { callback(); },
    scrollTo() {}, getComputedStyle() { return { display: 'block' }; },
    localStorage: { getItem() { return null; } },
    sessionStorage: { getItem() { return null; }, setItem() {} },
    alert() {}, confirm() { return true; },
  };
  context.window = context;
  vm.runInNewContext(SOURCE, context);
  (docEvents.DOMContentLoaded || []).forEach(handler => handler());
  function click(index, overrides = {}) {
    const event = { target: cards[index], button: 0, defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; }, ...overrides };
    (docEvents.click || []).forEach(handler => handler(event));
    return event;
  }
  function succeed(request, suffix) {
    const patient = `CASE-SYNTHETIC-${suffix}`;
    request.resolve({ ok: true, status: 200, redirected: false,
      url: new URL(request.url, location).href,
      text: async () => `<html><head><title>展示 ${suffix} · 工作台</title></head><body><section class="detail-panel" data-patient-id="${patient}" data-display-label="展示 ${suffix}" data-filter="v_and_i"></section></body></html>` });
  }
  return { context, document, state, pending, pushes, cards, click, succeed, windowEvents };
}

test('普通点击局部加载：请求期间旧详情可见，成功后一次替换并同步身份和历史', async () => {
  const p = page();
  const previous = p.state.detail;
  const event = p.click(1);
  assert.equal(event.defaultPrevented, true, '患者点击应阻止整页跳转');
  assert.equal(p.pending.length, 1);
  assert.equal(new URL(p.pending[0].url, p.context.location).pathname, '/workbench/CASE-SYNTHETIC-B');
  assert.equal(p.state.detail, previous);
  assert.equal(previous.inert, true, '旧详情保持可见，但请求期间不能继续修改或提交');
  assert.equal(p.context.JAVERT_PATIENT, 'CASE-SYNTHETIC-A');
  assert.equal(p.pushes.length, 0);
  p.succeed(p.pending[0], 'B');
  await flush();
  assert.equal(p.state.replacements, 1);
  assert.equal(p.state.detail.inert, false);
  assert.equal(p.state.detail.dataset.patientId, 'CASE-SYNTHETIC-B');
  assert.equal(p.context.JAVERT_PATIENT, 'CASE-SYNTHETIC-B');
  assert.equal(p.context.JAVERT_DISPLAY_LABEL, '展示 B');
  assert.equal(p.document.title, '展示 B · 工作台');
  assert.equal(p.pushes.length, 1);
  assert.equal(p.context.location.pathname, '/workbench/CASE-SYNTHETIC-B');
  assert.equal(p.cards[0].classList.contains('active'), false);
  assert.equal(p.cards[1].classList.contains('active'), true);
  assert.equal(p.state.fullNavigations, 0);
});

test('请求失败保留当前详情与地址，不回退整页跳转', async () => {
  const p = page();
  const previous = p.state.detail;
  assert.equal(p.click(1).defaultPrevented, true);
  assert.equal(p.pending.length, 1);
  p.pending[0].reject(new Error('SYNTHETIC network failure'));
  await flush();
  assert.equal(p.state.detail, previous);
  assert.equal(previous.inert, false, '请求失败后恢复原详情交互');
  assert.equal(p.state.replacements, 0);
  assert.equal(p.context.JAVERT_PATIENT, 'CASE-SYNTHETIC-A');
  assert.equal(p.context.location.pathname, '/workbench/CASE-SYNTHETIC-A');
  assert.equal(p.pushes.length, 0);
  assert.equal(p.state.fullNavigations, 0);
});

test('连续点击最后一次获胜，先前请求迟到不能换回旧患者', async () => {
  const p = page();
  assert.equal(p.click(1).defaultPrevented, true);
  assert.equal(p.click(2).defaultPrevented, true);
  assert.equal(p.pending.length, 2);
  p.succeed(p.pending[1], 'C');
  await flush();
  p.succeed(p.pending[0], 'B');
  await flush();
  assert.equal(p.state.detail.dataset.patientId, 'CASE-SYNTHETIC-C');
  assert.equal(p.context.JAVERT_PATIENT, 'CASE-SYNTHETIC-C');
  assert.equal(p.context.location.pathname, '/workbench/CASE-SYNTHETIC-C');
  assert.equal(p.state.replacements, 1);
  assert.equal(p.pushes.length, 1);
});

test('修饰键与中键点击保留浏览器新标签页行为', () => {
  const p = page();
  for (const override of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
    assert.equal(p.click(1, override).defaultPrevented, false);
  }
  assert.equal(p.pending.length, 0);
});

test('HTTP 错误或缺少详情的响应不替换现有患者', async () => {
  for (const response of [
    { ok: false, status: 500, text: async () => 'SYNTHETIC failure' },
    { ok: true, status: 200, redirected: false, text: async () => '<html><title>无效页面</title></html>' },
  ]) {
    const p = page();
    const previous = p.state.detail;
    assert.equal(p.click(1).defaultPrevented, true);
    p.pending[0].resolve(response);
    await flush();
    assert.equal(p.state.detail, previous);
    assert.equal(p.context.JAVERT_PATIENT, 'CASE-SYNTHETIC-A');
    assert.equal(p.pushes.length, 0);
    assert.equal(p.state.fullNavigations, 0);
  }
});

test('浏览器返回局部恢复患者，不新增历史项', async () => {
  const p = page();
  assert.equal(p.click(1).defaultPrevented, true);
  p.succeed(p.pending[0], 'B');
  await flush();
  p.context.location.href = 'https://example.test/workbench/CASE-SYNTHETIC-A?filter=v_and_i';
  assert.ok((p.windowEvents.popstate || []).length > 0);
  p.windowEvents.popstate.forEach(handler => handler({ state: null }));
  assert.equal(p.pending.length, 2);
  assert.equal(p.state.detail.dataset.patientId, 'CASE-SYNTHETIC-B');
  p.succeed(p.pending[1], 'A');
  await flush();
  assert.equal(p.state.detail.dataset.patientId, 'CASE-SYNTHETIC-A');
  assert.equal(p.context.JAVERT_PATIENT, 'CASE-SYNTHETIC-A');
  assert.equal(p.pushes.length, 1);
  assert.equal(p.state.fullNavigations, 0);
});

test('取消放弃未提交评语时不请求新患者，保留评语和详情', () => {
  const p = page();
  const previous = p.state.detail;
  const comment = { type: 'textarea', value: 'SYNTHETIC 未提交评语', defaultValue: '' };
  previous.querySelectorAll = selector => selector.includes('.review-form') ? [comment] : [];
  let confirmations = 0;
  p.context.confirm = () => { confirmations += 1; return false; };
  assert.equal(p.click(1).defaultPrevented, true);
  assert.equal(confirmations, 1);
  assert.equal(p.pending.length, 0);
  assert.equal(p.state.detail, previous);
  assert.equal(comment.value, 'SYNTHETIC 未提交评语');
  assert.equal(p.context.location.pathname, '/workbench/CASE-SYNTHETIC-A');
});

test('A 到 B 再回 A 后，第一代 A 原文迟到也不能重新打开 modal 或对照面板', async () => {
  const p = page();
  p.context.showRawData('CASE-SYNTHETIC-A');
  p.context.openSourcePanel({ tab: 'notes' });
  assert.equal(p.pending.length, 1, '两个原文入口复用同一次请求');
  p.click(1);
  p.succeed(p.pending[1], 'B');
  await flush();
  p.click(0);
  p.succeed(p.pending[2], 'A');
  await flush();
  p.pending[0].resolve({ ok: true, json: async () => ({ tab: 'notes', notes: [] }) });
  await flush();
  assert.equal(p.document.getElementById('modal-root').innerHTML, '');
  assert.equal(p.document.getElementById('source-panel').innerHTML, '');
  assert.equal(p.document.body.classList.contains('compare-open'), false);
  p.context.showRawData('CASE-SYNTHETIC-A');
  assert.equal(p.pending.length, 4, '第一代迟到原文也不能污染新患者页缓存');
});

test('旧侧栏请求晚到，active 仍跟随已经切换成功的新患者', async () => {
  const p = page();
  for (const id of ['patient-list', 'sidebar-status', 'sidebar-retry', 'sidebar-prev',
    'sidebar-next', 'patient-count', 'sidebar-page', 'facet-tags']) {
    p.state.elements.set(id, element({ id }));
  }
  const list = p.state.elements.get('patient-list');
  Object.defineProperty(list, 'innerHTML', {
    set(html) {
      const active = [...html.matchAll(/class="patient-card active" data-patient-id="([^"]+)"/g)].map(match => match[1]);
      p.cards.forEach(card => card.classList.toggle('active', active.includes(card.getAttribute('data-patient-id'))));
    },
  });
  p.context.reloadPatientSidebar();
  assert.equal(p.pending.length, 1);
  assert.equal(new URL(p.pending[0].url, p.context.location).searchParams.get('active_patient'), 'CASE-SYNTHETIC-A');
  p.click(1);
  p.succeed(p.pending[1], 'B');
  await flush();
  p.pending[0].resolve({ ok: true, json: async () => ({
    html: p.cards.map((card, index) => `<a class="patient-card${index === 0 ? ' active' : ''}" data-patient-id="${card.getAttribute('data-patient-id')}"></a>`).join(''),
    page: 1, pages: 1, total: 3, unfiltered_total: 3, tags: [],
  }) });
  await flush();
  assert.equal(p.cards[0].classList.contains('active'), false);
  assert.equal(p.cards[1].classList.contains('active'), true);
  assert.equal(p.context.JAVERT_PATIENT, 'CASE-SYNTHETIC-B');
});

test('返回 A 请求中再次点击当前 B，应取消返回并把地址恢复为 B', async () => {
  const p = page();
  p.click(1);
  p.succeed(p.pending[0], 'B');
  await flush();
  p.context.location.href = 'https://example.test/workbench/CASE-SYNTHETIC-A?filter=v_and_i';
  p.windowEvents.popstate.forEach(handler => handler({ state: null }));
  assert.equal(p.pending.length, 2);
  p.click(1);
  assert.equal(p.pending[1].options.signal.aborted, true);
  assert.equal(p.context.location.pathname, '/workbench/CASE-SYNTHETIC-B');
  assert.equal(p.context.JAVERT_PATIENT, 'CASE-SYNTHETIC-B');
  assert.equal(p.state.detail.inert, false);
  p.succeed(p.pending[1], 'A');
  await flush();
  assert.equal(p.state.detail.dataset.patientId, 'CASE-SYNTHETIC-B');
  assert.equal(p.context.location.pathname, '/workbench/CASE-SYNTHETIC-B');
});

test('返回 A 请求中改点 C 但 C 失败，详情和地址都应保留在 B', async () => {
  const p = page();
  p.click(1);
  p.succeed(p.pending[0], 'B');
  await flush();
  p.context.location.href = 'https://example.test/workbench/CASE-SYNTHETIC-A?filter=v_and_i';
  p.windowEvents.popstate.forEach(handler => handler({ state: null }));
  p.click(2);
  assert.equal(p.pending.length, 3);
  assert.equal(p.pending[1].options.signal.aborted, true);
  p.pending[2].reject(new Error('SYNTHETIC network failure'));
  await flush();
  assert.equal(p.context.location.pathname, '/workbench/CASE-SYNTHETIC-B');
  assert.equal(p.context.JAVERT_PATIENT, 'CASE-SYNTHETIC-B');
  assert.equal(p.state.detail.dataset.patientId, 'CASE-SYNTHETIC-B');
  assert.equal(p.state.detail.inert, false);
  p.succeed(p.pending[1], 'A');
  await flush();
  assert.equal(p.state.detail.dataset.patientId, 'CASE-SYNTHETIC-B');
  assert.equal(p.context.location.pathname, '/workbench/CASE-SYNTHETIC-B');
});
