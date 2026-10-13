// 使用真实 app.js 的点击入口，断言患者切换期间旧详情一直可见。
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const SOURCE = fs.readFileSync(path.join(__dirname, '../../src/javert/web/static/app.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

test('首次 A 到 B 再回 A 使用标签页短期缓存，不再请求已经看过的详情', async () => {
  const p = page();
  p.click(1);
  p.succeed(p.pending[0], 'B');
  await flush();
  p.click(0);
  await flush();
  assert.equal(p.pending.length, 1, '只有首次打开 B 请求详情；回到初始 A 应命中缓存');
  assert.equal(p.state.detail.dataset.patientId, 'CASE-SYNTHETIC-A');
  assert.equal(p.context.location.pathname, '/workbench/CASE-SYNTHETIC-A');
});

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

function page(options = {}) {
  const pending = [], pushes = [], docEvents = {}, windowEvents = {};
  const state = { detail: null, replacements: 0, fullNavigations: 0, elements: new Map(),
    now: 1700000000000, intervals: [] };
  if (options.autoRefresh) state.elements.set('patient-list', element({ id: 'patient-list' }));
  const location = new URL(options.href || 'https://example.test/workbench/CASE-SYNTHETIC-A?filter=v_and_i');
  location.assign = () => { state.fullNavigations += 1; };
  location.reload = () => { state.fullNavigations += 1; };
  const body = element({ class: 'theme-blue' });
  const modalRoot = element();
  const filterSelect = element(); filterSelect.value = 'v_and_i';
  const filterForm = element({ action: location.pathname });
  const sidebar = element({ id: 'sidebar', 'data-filter': 'v_and_i' });
  sidebar.querySelector = selector => selector.includes('select') ? filterSelect : null;
  const cards = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'].map(suffix => {
    const patient = `CASE-SYNTHETIC-${suffix}`;
    const card = element({ class: `patient-card${suffix === 'A' ? ' active' : ''}`,
      'data-patient-id': patient,
      href: `/workbench/${patient}?filter=v_and_i` });
    card.tagName = 'A';
    card.href = new URL(card.getAttribute('href'), location).href;
    card.closest = selector => selector.includes('patient-card') ? card : (selector.includes('sidebar') ? sidebar : null);
    return card;
  });
  function detail(patient, label = patient, filter = 'v_and_i', user = 'SYNTHETIC', content = '') {
    const node = element({ class: 'detail-panel', id: 'detail-panel',
      'data-patient-id': patient, 'data-display-label': label, 'data-filter': filter, 'data-user': user });
    node.dataset = { patientId: patient, displayLabel: label, filter, user };
    node.innerHTML = content || `详情 ${label}<form class="review-form"><textarea name="comment"></textarea></form>`;
    const comment = (node.innerHTML.match(/<textarea[^>]*>([\s\S]*?)<\/textarea>/) || [])[1] || '';
    node.reviewFields = [{ type: 'textarea', value: comment, defaultValue: comment }];
    node.querySelectorAll = selector => selector.includes('.review-form') ? node.reviewFields : [];
    Object.defineProperty(node, 'outerHTML', {
      get() { return '<section ' + Object.entries(node.attributes).map(([key, value]) => `${key}="${value}"`).join(' ') + `>${node.innerHTML}</section>`; },
    });
    node.cloneNode = () => {
      const clone = detail(patient, label, filter, user, node.innerHTML);
      clone.reviewFields = node.reviewFields.map(field => ({ ...field }));
      return clone;
    };
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
    readyState: 'loading', title: 'CASE-SYNTHETIC-A · 工作台', body, hidden: false,
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
  document.documentElement = { get outerHTML() { return `<html><head><title>${document.title}</title></head><body>${state.detail.outerHTML}</body></html>`; } };
  const context = {
    document, location, URL, URLSearchParams, AbortController,
    JAVERT_PATIENT: 'CASE-SYNTHETIC-A', JAVERT_DISPLAY_LABEL: 'CASE-SYNTHETIC-A', JAVERT_USER: 'SYNTHETIC',
    Date: class extends Date {
      constructor(...args) { super(...(args.length ? args : [state.now])); }
      static now() { return state.now; }
    },
    performance: { now: () => state.now },
    EventSource: class {
      constructor(url) { this.url = url; this.listeners = {}; state.stream = this; }
      addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
      close() { this.closed = true; }
    },
    history: {
      pushState(data, title, href) { pushes.push({ data, title, href }); location.href = new URL(href, location).href; },
      replaceState(data, title, href) { if (href) location.href = new URL(href, location).href; },
    },
    DOMParser: class {
      parseFromString(html) {
        const section = (html.match(/<section\b[^>]*class="detail-panel"[^>]*>/) || [])[0] || '';
        const attrs = Object.fromEntries([...section.matchAll(/(data-[\w-]+)="([^"]*)"/g)].map(match => [match[1], match[2]]));
        const content = (html.match(/<section\b[^>]*>([\s\S]*?)<\/section>/) || [])[1] || '';
        const panel = Object.hasOwn(attrs, 'data-patient-id') ? detail(attrs['data-patient-id'], attrs['data-display-label'], attrs['data-filter'], attrs['data-user'], content) : null;
        return { title: (html.match(/<title>(.*?)<\/title>/) || [])[1] || '',
          querySelector(selector) { return selector === '.detail-panel' || selector === '#detail-panel' ? panel : null; } };
      }
    },
    fetch(url, options) { return new Promise((resolve, reject) => pending.push({ url, options, resolve, reject })); },
    addEventListener(name, handler) { (windowEvents[name] ||= []).push(handler); },
    setTimeout() {}, clearTimeout() {}, setInterval(callback) { state.intervals.push(callback); }, clearInterval() {},
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
  function succeed(request, suffix, responseOptions = {}) {
    const patient = `CASE-SYNTHETIC-${suffix}`;
    request.resolve({ ok: true, status: 200, redirected: false,
      url: new URL(request.url, location).href,
      text: async () => `<html><head><title>展示 ${suffix} · 工作台</title></head><body><section class="detail-panel" data-patient-id="${patient}" data-display-label="展示 ${suffix}" data-filter="${responseOptions.filter || 'v_and_i'}" data-user="${responseOptions.user || context.JAVERT_USER}">${responseOptions.content || ''}</section></body></html>` });
  }
  function emitSse(name, data = {}) {
    const event = { data: JSON.stringify(data) };
    (state.stream.listeners[name] || []).forEach(handler => handler(event));
    if (typeof state.stream[`on${name}`] === 'function') state.stream[`on${name}`](event);
  }
  function visibility(hidden) {
    document.hidden = hidden;
    (docEvents.visibilitychange || []).forEach(handler => handler({ type: 'visibilitychange' }));
  }
  return { context, document, state, pending, pushes, cards, click, succeed, windowEvents,
    emitSse, visibility, advance(ms) { state.now += ms; } };
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
  p.advance(60001); // 初始 A 已过期，继续覆盖返回请求尚未完成的路径。
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
  p.advance(60001);
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
  p.advance(60001);
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
  p.advance(60001);
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

async function visit(p, index, options = {}) {
  const count = p.pending.length;
  const patient = p.cards[index].getAttribute('data-patient-id');
  p.click(index);
  await flush();
  if (p.pending.length > count) {
    p.succeed(p.pending.at(-1), patient.slice('CASE-SYNTHETIC-'.length), options);
    await flush();
  }
  assert.equal(p.context.JAVERT_PATIENT, patient, '请求或缓存命中都必须确实完成导航');
  assert.equal(p.state.detail.dataset.patientId, patient);
}

function patientHref(p, index, search) {
  p.cards[index].href = `https://example.test/workbench/${p.cards[index].getAttribute('data-patient-id')}${search}`;
  p.cards[index].setAttribute('href', p.cards[index].href);
}

test('缓存固定 60 秒到期，反复命中不会延长旧详情寿命', async () => {
  const p = page();
  await visit(p, 1);
  p.advance(59000);
  await visit(p, 0);
  await visit(p, 1);
  assert.equal(p.pending.length, 1, '60 秒内命中不重新请求');
  p.advance(1001);
  await visit(p, 0);
  await visit(p, 1);
  assert.equal(p.pending.length, 3, 'A 和 B 均超过首次写入后的 60 秒，必须重新请求');
});

test('URL 参数顺序与 hash 不重复缓存，但 filter、batch 和用户分别隔离', async () => {
  const p = page();
  patientHref(p, 1, '?batch_tag=SYNTHETIC&filter=all#run_one');
  await visit(p, 1, { filter: 'all' });
  await visit(p, 0);
  patientHref(p, 1, '?filter=all&batch_tag=SYNTHETIC#run_two');
  await visit(p, 1, { filter: 'all' });
  assert.equal(p.pending.length, 1, '同一参数集合不同顺序/锚点仍命中');
  await visit(p, 0);
  patientHref(p, 1, '?filter=v_only&batch_tag=SYNTHETIC');
  await visit(p, 1, { filter: 'v_only' });
  assert.equal(p.pending.length, 2, 'filter 变化必须请求');
  await visit(p, 0);
  patientHref(p, 1, '?filter=v_only&batch_tag=SYNTHETIC-OTHER');
  await visit(p, 1, { filter: 'v_only' });
  assert.equal(p.pending.length, 3, 'batch 变化必须请求');
  await visit(p, 0);
  p.context.JAVERT_USER = 'SYNTHETIC-OTHER-USER';
  await visit(p, 1, { filter: 'v_only' });
  assert.equal(p.pending.length, 4, '不能给其他用户复用已缓存页面');
});

test('初始无 filter URL 按实际页面 filter 种入缓存，原无 filter 历史入口仍请求', async () => {
  const p = page({ href: 'https://example.test/workbench/CASE-SYNTHETIC-A' });
  await visit(p, 1);
  await visit(p, 0);
  assert.equal(p.pending.length, 1, '侧栏带显式 filter 的 A 链接可命中初始快照');
  await visit(p, 1);
  p.context.location.href = 'https://example.test/workbench/CASE-SYNTHETIC-A';
  p.windowEvents.popstate.forEach(handler => handler({ state: null }));
  await flush();
  assert.equal(p.pending.length, 2, '缺省 filter 可能随 cookie 改变，返回时必须请求');
});

test('审核和新审计 SSE 使已有详情缓存失效', async () => {
  for (const name of ['review_submitted', 'new_audit_run']) {
    const p = page();
    await visit(p, 1);
    await visit(p, 0);
    assert.equal(p.pending.length, 1);
    p.emitSse(name, { patient_id: 'CASE-SYNTHETIC-B', rule_id: 'R191', run_id: 'aud_SYNTHETIC', reviewer_username: 'SYNTHETIC' });
    p.click(1);
    assert.equal(p.pending.length, 2, name + ' 后不能使用旧 B');
  }
});

test('详情在途时收到 SSE 失效，迟到响应不得重新填入缓存', async () => {
  const p = page();
  p.click(1);
  p.emitSse('new_audit_run', { patient_id: 'CASE-SYNTHETIC-B', rule_id: 'R191' });
  p.succeed(p.pending[0], 'B');
  await flush();
  await visit(p, 2);
  const count = p.pending.length;
  p.click(1);
  await flush();
  assert.equal(p.pending.length, count + 1, '在事件前开始的响应不允许作为事件后的缓存');
});

test('SSE 首次连接保留初始缓存，断线重连、恢复可见和窗口聚焦后清空', async () => {
  for (const invalidate of [
    p => { p.emitSse('error'); p.emitSse('open'); },
    p => { p.visibility(true); p.visibility(false); },
    p => { (p.windowEvents.focus || []).forEach(handler => handler({ type: 'focus' })); },
  ]) {
    const p = page();
    p.emitSse('open');
    await visit(p, 1);
    await visit(p, 0);
    assert.equal(p.pending.length, 1, '首次 open 不应丢掉初始 A');
    invalidate(p);
    p.click(1);
    assert.equal(p.pending.length, 2, '恢复后需读取事件缺口期间的最新详情');
  }
});

test('缓存最多 5 页，命中会保留最近使用的页面并淘汰最久未使用页', async () => {
  const p = page();
  for (const index of [1, 2, 3, 4]) await visit(p, index);
  await visit(p, 0);
  assert.equal(p.pending.length, 4);
  await visit(p, 5);
  await visit(p, 0);
  assert.equal(p.pending.length, 5, '最近命中的 A 应保留');
  await visit(p, 1);
  assert.equal(p.pending.length, 6, '第六页进入后最久未使用的 B 应淘汰');
});

test('缓存 HTML 字符总量受限，超过单页上限的大页也不缓存', async () => {
  const p = page();
  for (const index of [1, 2, 3]) await visit(p, index, { content: 'S'.repeat(800000) });
  await visit(p, 2);
  assert.equal(p.pending.length, 3, '未超过总量的新近 C 仍可复用');
  await visit(p, 1);
  assert.equal(p.pending.length, 4, '约 240 万字符超过 2 Mi 字符上限，最旧 B 必须淘汰');

  const big = page();
  await visit(big, 1, { content: 'S'.repeat(2 * 1024 * 1024 + 1) });
  await visit(big, 0);
  assert.equal(big.pending.length, 1, '拒存超大 B 不应挤掉已有 A');
  await visit(big, 1);
  assert.equal(big.pending.length, 2, '单页超过字符上限应重新请求');
});

test('失败或身份不匹配 HTML 不写入缓存，再次点击可获取有效详情', async () => {
  for (const response of [
    { ok: false, status: 500, text: async () => 'SYNTHETIC failure' },
    { ok: true, status: 200, text: async () => '<html><title>无详情</title></html>' },
    { ok: true, status: 200, text: async () => '<section class="detail-panel" data-patient-id="CASE-SYNTHETIC-C" data-user="SYNTHETIC"></section>' },
  ]) {
    const p = page();
    p.click(1);
    p.pending[0].resolve(response);
    await flush();
    p.click(1);
    assert.equal(p.pending.length, 2);
    p.succeed(p.pending[1], 'B');
    await flush();
    await visit(p, 0);
    await visit(p, 1);
    assert.equal(p.pending.length, 2, '只有成功且身份正确的响应才可复用');
  }
});

test('确认丢弃评语后返回缓存快照，不复活上次草稿', async () => {
  const p = page();
  p.state.detail.reviewFields[0].value = 'SYNTHETIC 已丢弃草稿';
  let confirmations = 0;
  p.context.confirm = () => { confirmations += 1; return true; };
  await visit(p, 1);
  await visit(p, 0);
  assert.equal(confirmations, 1);
  assert.equal(p.pending.length, 1);
  assert.equal(p.state.detail.reviewFields[0].value, '');
  assert.doesNotMatch(p.state.detail.outerHTML, /已丢弃草稿/);
});

test('其它患者 SSE 不清 B，B 自身的事件失效 B 的全部筛选变体', async () => {
  const p = page();
  await visit(p, 1);
  patientHref(p, 1, '?filter=all&batch_tag=SYNTHETIC');
  await visit(p, 1, { filter: 'all' });
  await visit(p, 2);
  p.emitSse('review_submitted', { patient_id: 'CASE-SYNTHETIC-A', run_id: 'aud_SYNTHETIC', rule_id: 'R191' });
  await visit(p, 1, { filter: 'all' });
  assert.equal(p.pending.length, 3, 'A 的审核事件不能清掉 B');
  p.emitSse('new_audit_run', { patient_id: 'CASE-SYNTHETIC-B', rule_id: 'R191' });
  await visit(p, 2);
  await visit(p, 1, { filter: 'all' });
  assert.equal(p.pending.length, 4, 'B 的事件使 all/batch 变体失效');
  patientHref(p, 1, '?filter=v_and_i');
  await visit(p, 1);
  assert.equal(p.pending.length, 5, 'B 的旧 v_and_i 变体也不能保留');
});

test('SSE 断线期间禁用缓存读写，恢复后重新获取的详情才可复用', async () => {
  const p = page();
  p.emitSse('open');
  await visit(p, 1);
  await visit(p, 0);
  assert.equal(p.pending.length, 1);
  p.emitSse('error');
  await visit(p, 1);
  await visit(p, 2);
  await visit(p, 1);
  assert.equal(p.pending.length, 4, '断线期间已看患者仍应请求，期间响应不得缓存');
  p.emitSse('open');
  await visit(p, 2);
  await visit(p, 1);
  await visit(p, 2);
  assert.equal(p.pending.length, 6, '恢复后只复用重新获取的页面');
});

test('缓存保存初始和请求响应的快照，之后的客户端 DOM 变化不会跟着恢复', async () => {
  const p = page();
  p.state.detail.innerHTML += '<span>SYNTHETIC-A-客户端临时状态</span>';
  await visit(p, 1);
  p.state.detail.innerHTML += '<span>SYNTHETIC-B-客户端临时状态</span>';
  p.state.detail.reviewFields[0].value = 'SYNTHETIC-B-未提交草稿';
  await visit(p, 0);
  assert.doesNotMatch(p.state.detail.outerHTML, /客户端临时状态/);
  await visit(p, 1);
  assert.equal(p.pending.length, 1);
  assert.doesNotMatch(p.state.detail.outerHTML, /客户端临时状态|未提交草稿/);
  assert.equal(p.state.detail.reviewFields[0].value, '');
});

test('轮询签名未变或为空保持缓存，签名变化则重新请求详情', async () => {
  const p = page({ autoRefresh: true });
  assert.equal(p.pending[0].url, '/api/workbench/sig');
  p.pending[0].resolve({ ok: true, json: async () => ({ sig: 1 }) });
  await flush();
  await visit(p, 1);
  await visit(p, 0);
  const detailRequests = () => p.pending.filter(request => new URL(request.url, p.context.location).pathname.startsWith('/workbench/')).length;
  assert.equal(detailRequests(), 1);
  for (const sig of [1, null]) {
    p.state.intervals[0]();
    p.pending.at(-1).resolve({ ok: true, json: async () => ({ sig }) });
    await flush();
    await visit(p, 1);
    await visit(p, 0);
    assert.equal(detailRequests(), 1, '签名不变或暂不可用不应丢掉缓存');
  }
  p.state.intervals[0]();
  p.pending.at(-1).resolve({ ok: true, json: async () => ({ sig: 2 }) });
  await flush();
  await visit(p, 1);
  assert.equal(detailRequests(), 2, '签名变化必须读取新详情');
});

test('原文请求发现登录失效后，不再从详情缓存打开其他患者', async () => {
  const p = page();
  await visit(p, 1);
  await visit(p, 0);
  assert.equal(p.pending.length, 1);
  p.context.showRawData('CASE-SYNTHETIC-A');
  assert.match(p.pending[1].url, /^\/api\/patient\/CASE-SYNTHETIC-A\/raw/);
  p.pending[1].resolve({ ok: false, status: 401, json: async () => ({ detail: 'SYNTHETIC session expired' }) });
  await flush();
  p.click(1);
  await flush();
  assert.equal(p.pending.length, 3, '检测到 401 后，旧 B 缓存不能继续提供详情');
  assert.equal(p.pending[2].url, p.cards[1].href);
  assert.equal(p.context.JAVERT_PATIENT, 'CASE-SYNTHETIC-A', '等待重新鉴权响应时不展示旧缓存 B');
});

test('日期筛选按日期参数分页，切换详情后保留日期提示', async () => {
  const p = page();
  const chip = element({class: 'facet-chip', 'data-date': '2026-09-20'});
  const original = p.document.querySelectorAll;
  p.document.querySelectorAll = selector => selector === '.facet-chip.active' ?
    (chip.classList.contains('active') ? [chip] : []) : original(selector);
  const note = element();
  p.state.elements.set('date-filter-note', note);
  for (const id of ['patient-list', 'sidebar-status', 'sidebar-retry', 'sidebar-prev',
    'sidebar-next', 'patient-count', 'sidebar-page', 'facet-tags']) {
    p.state.elements.set(id, element());
  }
  p.context.toggleTagChip(chip);
  assert.equal(chip.getAttribute('aria-pressed'), 'true');
  assert.equal(note.hidden, false);
  p.context.reloadPatientSidebar();
  const url = new URL(p.pending[0].url, p.context.location);
  assert.deepEqual(url.searchParams.getAll('date'), ['2026-09-20']);
  assert.equal(url.searchParams.has('tag'), false);
  await visit(p, 1);
  assert.equal(note.hidden, false);
  p.context.clearFacets();
  assert.equal(note.hidden, true);
});
