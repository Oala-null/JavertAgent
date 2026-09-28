// 运行真实 app.js 的公开原文入口；重复点击共用请求，失败后允许重试。
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function page() {
  const pending = [];
  const root = { innerHTML: '' };
  const context = {
    document: { readyState: 'loading', addEventListener() {},
      getElementById: () => root, querySelector: () => null,
      querySelectorAll: () => [], head: { insertAdjacentHTML() {} } },
    fetch(url) { return new Promise((resolve, reject) => pending.push({ url, resolve, reject })); },
    setTimeout() {}, clearTimeout() {}, alert() {},
  };
  context.window = context;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../src/javert/web/static/app.js'), 'utf8'), context);
  return { context, pending, root };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('原文并发点击只请求一次，完成后复用结果，患者之间隔离', async () => {
  const { context, pending, root } = page();
  context.showRawData('CASE-SYNTHETIC-A');
  context.showRawData('CASE-SYNTHETIC-A');
  assert.equal(pending.length, 1);
  pending[0].resolve({ ok: true, json: async () => ({ notes: [], fees: [], labs: [] }) });
  await flush();
  assert.match(root.innerHTML, /CASE-SYNTHETIC-A/);
  context.showRawData('CASE-SYNTHETIC-A');
  assert.equal(pending.length, 1);
  context.showRawData('CASE-SYNTHETIC-B');
  assert.equal(pending.length, 2);
});

test('失败请求不缓存，下次点击可重试', async () => {
  const { context, pending } = page();
  context.showRawData('CASE-SYNTHETIC-FAIL');
  pending[0].reject(new Error('SYNTHETIC network failure'));
  await flush();
  context.showRawData('CASE-SYNTHETIC-FAIL');
  assert.equal(pending.length, 2);
});
