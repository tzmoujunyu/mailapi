const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const fixture = () => [{
  key: 'email:user@example.com', email: 'user@example.com',
  has_gpt_password: true, has_totp_secret: true,
  gpt_password_account: 'email:user@example.com', totp_secret_account: 'email:user@example.com',
  codex: [{ id: 'codex-1', email: 'user@example.com', chatgpt_plan_type: 'pro', usage_status: 'available', usage: { used_percent: 25 } }],
  mail: [{ name: 'account-1', email: 'user@example.com', provider: 'gmail', status: '监听中', last_code: '123456' }],
}, {
  key: 'email:only@example.com', email: 'only@example.com',
  has_gpt_password: true, has_totp_secret: true,
  gpt_password_account: 'email:only@example.com', totp_secret_account: 'email:only@example.com',
  codex: [{ id: 'codex-2', email: 'only@example.com', usage_status: 'unavailable', status_reason: 'usage_missing_weekly' }], mail: [],
}];
const flush = () => new Promise(resolve => setImmediate(resolve));
function page(file) {
  const nodes = new Map();
  const calls = [];
  const timers = new Map();
  let counter = 0;
  const element = () => ({
    innerHTML: '', textContent: '', dataset: {}, value: '', listeners: {},
    addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); },
    querySelector() { return null; },
    showModal() { this.open = true; }, close() { this.open = false; }, focus() {},
  });
  const document = { ...element(), getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
    createElement() { const node = element(); Object.defineProperty(node, 'textContent', { set(text) { this.innerHTML = String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); } }); return node; },
  };
  const context = vm.createContext({ document, console, Date, Map, Set, Promise,
    setInterval(callback) { timers.set(++counter, callback); return counter; }, clearInterval(id) { timers.delete(id); },
    setTimeout(callback) { return ++counter; },
    navigator: { clipboard: { async writeText(value) { calls.push(['clipboard', value]); } } }, window: { isSecureContext: true },
    confirm: () => true,
    fetch: async (url, options) => { calls.push([url, options]); return { ok: true, json: async () => ({ items: url.includes('history') ? [{ code: '654321', subject: 'Historical code' }] : fixture(), password: 'password-value', code: '081804' }) }; },
  });
  vm.runInContext(fs.readFileSync(file, 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1], context);
  async function click(id, selector, dataset) {
    const button = { dataset, textContent: '复制', disabled: false };
    const event = { target: { closest: query => query === selector ? button : null }, preventDefault() {} };
    for (const listener of nodes.get(id).listeners.click || []) await listener(event);
    if (id !== 'document') for (const listener of document.listeners.click || []) await listener(event);
    await flush();
    return button;
  }
  return { context, nodes, calls, timers, click };
}

test('homepage expands immediately, places history below email row, and collapses all details', async () => {
  const app = page('static/index.html'); await flush();
  assert.match(app.nodes.get('rows').innerHTML, /75%/);
  const count = app.calls.length;
  await app.click('rows', '[data-expand-key]', { expandKey: 'email:user@example.com' });
  assert.equal(app.calls.length, count);
  assert.match(app.nodes.get('rows').innerHTML, /GPT密码/);
  await app.click('rows', '[data-history-name]', { historyName: 'account-1' });
  const html = app.nodes.get('rows').innerHTML;
  assert.ok(html.indexOf('历史记录：') < html.indexOf('history-inline'));
  assert.match(html, /654321/);
  assert.match(html, /aria-expanded="true" data-history-name="account-1">收起/);
  await app.click('rows', '[data-history-name]', { historyName: 'account-1' });
  assert.doesNotMatch(app.nodes.get('rows').innerHTML, /654321/);
  await app.click('rows', '[data-expand-key]', { expandKey: 'email:user@example.com' });
  assert.doesNotMatch(app.nodes.get('rows').innerHTML, /GPT密码/);
  assert.equal(app.timers.size, 1);
});

test('Codex-only account can copy both credentials and shows neutral email status', async () => {
  const app = page('static/index.html'); await flush();
  await app.click('rows', '[data-expand-key]', { expandKey: 'email:only@example.com' });
  assert.match(app.nodes.get('rows').innerHTML, /status-neutral">无邮箱验证/);
  assert.match(app.nodes.get('rows').innerHTML, /data-name="email:only@example.com"/);
  for (const type of ['password', 'totp']) await app.click('rows', '[data-copy-type]', { copyType: type, name: 'email:only@example.com' });
  assert.ok(app.calls.some(([url]) => url.endsWith('/totp-code')));
  assert.ok(!app.calls.some(([url]) => url.endsWith('/totp-secret')));
  assert.deepEqual(app.calls.filter(call => call[0] === 'clipboard').map(call => call[1]), ['password-value', '081804']);
});

test('late history response cannot reopen collapsed content', async () => {
  const app = page('static/index.html'); await flush();
  await app.click('rows', '[data-expand-key]', { expandKey: 'email:user@example.com' });
  let resolve;
  app.context.fetch = () => new Promise(done => { resolve = done; });
  await app.click('rows', '[data-history-name]', { historyName: 'account-1' });
  await app.click('rows', '[data-expand-key]', { expandKey: 'email:user@example.com' });
  resolve({ ok: true, json: async () => ({ items: [{ code: 'late-code' }] }) }); await flush();
  assert.doesNotMatch(app.nodes.get('rows').innerHTML, /late-code|历史验证码|GPT密码/);
});

test('refresh failure preserves current account rows and displays an error', async () => {
  const app = page('static/index.html'); await flush();
  const html = app.nodes.get('rows').innerHTML;
  app.context.fetch = async () => ({ ok: false });
  await vm.runInContext('refreshAccounts()', app.context);
  assert.equal(app.nodes.get('rows').innerHTML, html);
  assert.match(app.nodes.get('load-error').textContent, /刷新失败/);
});

test('console uses unified rows and routes credential changes and integration deletion', async () => {
  const app = page('static/admin.html'); await flush();
  const html = app.nodes.get('account-rows').innerHTML;
  assert.equal((html.match(/<tr>/g) || []).length, 2);
  assert.match(html, /GPT密码/); assert.match(html, /移除邮箱/); assert.match(html, /移除 Codex/); assert.match(html, /删除整个账号/);
  await app.click('account-rows', '[data-password-action]', { passwordAction: 'edit', name: 'email:only@example.com', email: 'only@example.com' });
  assert.equal(app.nodes.get('password-dialog').open, true);
  app.nodes.get('password-input').value = 'new-password';
  await app.nodes.get('password-form').listeners.submit[0]({ preventDefault() {} });
  assert.ok(app.calls.some(([url, options]) => url === '/api/admin/accounts/email%3Aonly%40example.com/gpt-password' && options.method === 'PUT'));
  await app.click('account-rows', '[data-delete-account]', { deleteAccount: 'email:user@example.com', email: 'user@example.com' });
  assert.ok(app.calls.some(([url, options]) => url === '/api/admin/accounts/email%3Auser%40example.com' && options.method === 'DELETE'));
});
