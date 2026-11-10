const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page(theme = 'system', signedIn = true) {
  const requests = [];
  const root = { dataset: { theme, themeAccount: signedIn ? 'signed-in' : '' } };
  const select = { value: '', addEventListener(type, listener) { this.change = listener; } };
  const status = { textContent: '' };
  const holder = { classList: { add() {} }, querySelector: selector => selector === 'select' ? select : status };
  const state = { fetch: async () => ({ ok: true, json: async () => ({ theme: select.value }) }) };
  const context = vm.createContext({ window: {}, document: { documentElement: root, querySelectorAll: () => [] },
    fetch: async (url, options) => { requests.push({ url, options }); return state.fetch(); } });
  vm.runInContext(fs.readFileSync((process.env.MAILAPI_UI_ROOT || 'static') + '/theme.js', 'utf8'), context);
  context.window.SiteTheme.mount(holder);
  return { root, select, status, requests, state };
}

test('theme control starts from the account preference and saves each of the three modes', async () => {
  const app = page('dark');
  assert.equal(app.select.value, 'dark');
  assert.equal(app.requests.length, 0);
  for (const theme of ['light', 'dark', 'system']) {
    app.select.value = theme;
    await app.select.change();
    assert.equal(app.root.dataset.theme, theme);
    assert.equal(app.status.textContent, '');
    const request = app.requests.at(-1);
    assert.equal(request.url, '/api/auth/preferences');
    assert.equal(request.options.method, 'PUT');
    assert.deepEqual(JSON.parse(request.options.body), { theme });
  }
});

test('theme selection is immediate while saving and returns to the saved value on failure', async () => {
  const app = page();
  let finish;
  app.state.fetch = () => new Promise(resolve => { finish = resolve; });
  app.select.value = 'dark';
  const pending = app.select.change();
  assert.equal(app.root.dataset.theme, 'dark');
  assert.equal(app.select.disabled, true);
  finish({ ok: false, json: async () => ({ detail: 'denied' }) });
  await pending;
  assert.equal(app.root.dataset.theme, 'system');
  assert.equal(app.select.value, 'system');
  assert.equal(app.select.disabled, false);
  assert.match(app.status.textContent, /保存失败/);
});

test('login theme changes stay on the page and do not write a signed-out account preference', async () => {
  const app = page('system', false);
  assert.equal(app.select.value, 'system');
  app.select.value = 'dark';
  await app.select.change();
  assert.equal(app.root.dataset.theme, 'dark');
  assert.equal(app.requests.length, 0);
  assert.equal(page('light').select.value, 'light');
});
