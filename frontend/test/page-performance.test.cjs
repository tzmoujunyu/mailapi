const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '../../mailapi_shared/static');
const source = name => fs.readFileSync(path.join(root, name), 'utf8');

function page(t, html) {
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'https://example.test', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  dom.window.eval(source('page-performance.js'));
  return dom.window;
}

test('identical polling keeps rows, focus, selection and search visibility without DOM mutations', async t => {
  const w = page(t, '<table><tbody><tr><td>loading</td></tr></tbody></table>');
  const tbody = w.document.querySelector('tbody');
  const rows = [{ key: 'a', html: '<tr><td><input value="selected"></td></tr>' },
    { key: 'b', html: '<tr><td>other</td></tr>' }];
  w.PagePerformance.renderRows(tbody, rows);
  const input = tbody.querySelector('input');
  input.focus(); input.setSelectionRange(1, 4);
  tbody.lastElementChild.hidden = true;
  const observer = new w.MutationObserver(() => {});
  observer.observe(tbody, { childList: true, subtree: true, attributes: true });
  for (let i = 0; i < 20; i++) w.PagePerformance.renderRows(tbody, rows);
  assert.equal(observer.takeRecords().length, 0);
  assert.equal(w.document.activeElement, input);
  assert.equal(input.selectionStart, 1);
  assert.equal(tbody.lastElementChild.hidden, true);
});

test('changing, removing, adding and reordering records leaves unaffected rows intact', t => {
  const w = page(t, '<table><tbody></tbody></table>');
  const tbody = w.document.querySelector('tbody');
  const row = (key, text = key) => ({ key, html: `<tr><td>${text}</td></tr>` });
  const render = rows => w.PagePerformance.renderRows(tbody, rows);
  render([row('a'), row('b'), row('c')]);
  const [a, b, c] = tbody.children;
  render([row('c'), row('a'), row('b', 'changed'), row('d')]);
  assert.equal(tbody.children[0], c);
  assert.equal(tbody.children[1], a);
  assert.notEqual(tbody.children[2], b);
  assert.deepEqual([...tbody.children].map(node => node.textContent), ['c', 'a', 'changed', 'd']);
  render([row('a')]);
  assert.equal(tbody.children.length, 1);
  assert.equal(tbody.firstElementChild, a);
  render([{ key: 'empty', html: '<tr><td colspan="4">empty</td></tr>' }]);
  render([row('a')]);
  assert.equal(tbody.textContent, 'a');
});

test('account detail rows and search-owned empty rows survive ordinary refreshes', t => {
  const w = page(t, '<table><tbody></tbody></table>');
  const tbody = w.document.querySelector('tbody');
  const rows = [{ key: 'a', html: '<tr><td>account</td></tr><tr><td>details</td></tr>' }];
  w.PagePerformance.renderRows(tbody, rows);
  const nodes = [...tbody.children];
  const empty = w.document.createElement('tr');
  empty.dataset.searchEmpty = ''; tbody.append(empty);
  w.PagePerformance.renderRows(tbody, rows);
  assert.deepEqual([...tbody.children], [...nodes, empty]);
});

test('lazy script loading deduplicates downloads and retries failed requests', async t => {
  const w = page(t, '');
  const first = w.PagePerformance.loadScript('/editor.js');
  assert.equal(w.PagePerformance.loadScript('/editor.js'), first);
  w.document.querySelector('script').dispatchEvent(new w.Event('error'));
  await assert.rejects(first, /加载失败/);
  const retry = w.PagePerformance.loadScript('/editor.js');
  assert.notEqual(first, retry);
  w.document.querySelector('script').dispatchEvent(new w.Event('load'));
  await retry;
  assert.equal(w.PagePerformance.loadScript('/editor.js'), retry);
  assert.equal(w.document.querySelectorAll('script').length, 1);
});

test('search debounces typing, searches hidden modules and updates its index after resource changes', async t => {
  const w = page(t, `<input id="management-search"><button id="management-search-clear"></button>
    <p id="management-search-status"></p><div id="management-search-results"></div>
    <section><table><thead><tr><th>name</th></tr></thead><tbody data-search-scope="accounts"><tr><td>alice</td></tr></tbody></table></section>
    <section hidden><table><thead><tr><th>link</th></tr></thead><tbody data-search-scope="resources"><tr data-search-text="小米"><td><a href="https://api.example.test">MiMo</a></td></tr></tbody></table></section>`);
  w.eval(source('management-search.js'));
  const input = w.document.querySelector('input');
  const enter = value => { input.value = value; input.dispatchEvent(new w.Event('input')); };
  enter('alice'); enter('小米');
  assert.equal(w.document.querySelector('[data-search-scope="accounts"] tr').hidden, false);
  await new Promise(resolve => setTimeout(resolve, 240));
  assert.equal(w.document.querySelector('[data-search-scope="accounts"] tr').hidden, true);
  const resource = w.document.querySelector('[data-search-scope="resources"] tr');
  assert.equal(resource.hidden, false);
  resource.dataset.searchText = '智谱';
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(resource.hidden, true);
  enter('api.example.test');
  await new Promise(resolve => setTimeout(resolve, 240));
  assert.equal(resource.hidden, false);
  w.document.getElementById('management-search-clear').click();
  assert.equal(w.document.querySelector('[data-search-scope="accounts"] tr').hidden, false);
});

async function application(t, file) {
  const html = source(file);
  const w = page(t, html);
  const requests = [], timers = [];
  const accounts = Array.from({ length: 100 }, (_, i) => ({ key: `account:${i}`, login: `preview${i}@example.test`,
    email: `preview${i}@example.test`, codex: [], mail: [], has_gpt_password: true, has_totp_secret: false, listed: true }));
  w.fetch = async url => { requests.push(url); return { ok: true, json: async () => ({ items: url === '/api/accounts' ? accounts : [] }) }; };
  w.setInterval = (callback, delay) => { timers.push({ callback, delay }); return timers.length; };
  w.clearInterval = () => {};
  w.scrollTo = () => {};
  w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  w.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new w.Event('close')); };
  w.ResizeObserver = class { observe() {} disconnect() {} };
  w.matchMedia = () => ({ matches: true });
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.Range.prototype.getClientRects = () => [];
  w.Range.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 });
  for (const name of ['coding-plan-quota.js', 'coding-plan-config.js', 'account-subscription.js', 'codex-tip-content.js', 'codex-tip-format.js']) w.eval(source(name));
  if (file === 'admin.html') {
    w.eval(source('management-tabs.js'));
    w.eval(source('management-search.js'));
  }
  w.eval(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle();
  return { w, accounts, requests, timers, settle };
}

test('real homepage with 100 accounts preserves expanded rows across polls and updates only the changed account', async t => {
  const { w, accounts, timers, settle } = await application(t, 'index.html');
  const rows = w.document.getElementById('rows');
  rows.querySelector('[data-expand-key]').click();
  const first = rows.firstElementChild, detail = first.nextElementSibling;
  const changes = [];
  const observer = new w.MutationObserver(records => changes.push(...records));
  observer.observe(rows, { childList: true, subtree: true });
  await timers.find(timer => timer.delay === 3000).callback();
  await settle();
  assert.equal(changes.length, 0);
  assert.equal(rows.firstElementChild, first);
  assert.equal(first.nextElementSibling, detail);
  const second = rows.children[2];
  accounts[1].login = accounts[1].email = 'changed@example.test';
  await w.eval('refreshAccounts()');
  assert.equal(rows.firstElementChild, first);
  assert.equal(first.nextElementSibling, detail);
  assert.notEqual(rows.children[2], second);
  assert.match(rows.children[2].textContent, /changed@example.test/);
  accounts.splice(0, 1);
  await w.eval('refreshAccounts()');
  assert.equal(rows.querySelector('.detail-row'), null);
  assert.equal(rows.children.length, 99);
});

test('real console switches panels, keeps filtered rows on polling and initializes the real editor on demand', async t => {
  const { w, requests, timers, settle } = await application(t, 'admin.html');
  assert.equal(w.document.getElementById('account-rows').children.length, 100);
  const search = w.document.getElementById('management-search');
  search.value = 'preview99'; search.dispatchEvent(new w.Event('input'));
  await new Promise(resolve => setTimeout(resolve, 240));
  const rows = w.document.getElementById('account-rows');
  assert.equal([...rows.children].filter(row => !row.hidden).length, 1);
  const selected = rows.children[99];
  await timers.find(timer => timer.delay === 5000).callback();
  await settle();
  assert.equal(rows.children[99], selected);
  assert.equal([...rows.children].filter(row => !row.hidden).length, 1);
  requests.length = 0;
  w.document.getElementById('coding-pool-tab').click();
  await settle();
  assert.equal(w.document.getElementById('coding-pool-tab').getAttribute('aria-selected'), 'true');
  assert.deepEqual(requests, ['/api/coding-plans']);
  assert.equal(w.document.querySelector('script[src="/static/vendor/tiptap/tiptap.js"]'), null);
  w.document.getElementById('management-search-clear').click();
  w.document.getElementById('codex-tips-tab').click();
  w.document.getElementById('add-codex-tip').click();
  const bundle = w.document.querySelector('script[src="/static/vendor/tiptap/tiptap.js"]');
  assert.ok(bundle);
  w.eval(source('vendor/tiptap/tiptap.js'));
  const RealEditor = w.MailapiTiptap.Editor;
  let editor;
  w.MailapiTiptap.Editor = class extends RealEditor { constructor(options) { super(options); editor = this; } };
  t.after(() => editor?.destroy());
  bundle.dispatchEvent(new w.Event('load'));
  await settle();
  w.eval(source('codex-tip-editor.js'));
  w.document.querySelector('script[src="/static/codex-tip-editor.js"]').dispatchEvent(new w.Event('load'));
  await settle();
  assert.equal(w.document.querySelectorAll('.ProseMirror').length, 1);
  assert.equal(w.document.getElementById('codex-tip-save').disabled, false);
  w.document.getElementById('codex-tip-cancel').click();
  w.document.getElementById('add-codex-tip').click();
  await settle();
  assert.equal(w.document.querySelectorAll('script[src="/static/vendor/tiptap/tiptap.js"]').length, 1);
  assert.equal(w.document.querySelectorAll('.ProseMirror').length, 1);
});
