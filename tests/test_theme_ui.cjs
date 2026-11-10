const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page(theme = 'system', signedIn = true, viewTransitions = false) {
  const requests = [];
  const root = { dataset: { theme, themeAccount: signedIn ? 'signed-in' : '' } };
  const button = { dataset: {}, attributes: {}, setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(type, listener) { this[type] = listener; } };
  const status = { textContent: '' };
  const current = { textContent: '' }, next = { textContent: '' };
  const holder = { classList: { add() {} }, querySelector: selector => ({ button,
    '[role="status"]': status, '[data-current-mode]': current, '[data-next-mode]': next })[selector] };
  const state = { transitions: 0, reduced: false,
    fetch: async () => ({ ok: true, json: async () => ({ theme: root.dataset.theme }) }) };
  const document = { documentElement: root, querySelectorAll: () => [] };
  if (viewTransitions) document.startViewTransition = update => {
    state.transitions++;
    return { updateCallbackDone: Promise.resolve().then(update), finished: Promise.resolve() };
  };
  const context = vm.createContext({ window: { matchMedia: () => ({ matches: state.reduced }) }, document,
    fetch: async (url, options) => { requests.push({ url, options }); return state.fetch(); } });
  vm.runInContext(fs.readFileSync((process.env.MAILAPI_UI_ROOT || 'static') + '/theme.js', 'utf8'), context);
  context.window.SiteTheme.mount(holder);
  return { root, button, status, requests, state, current, next, document };
}

test('theme icon starts from the saved mode and exposes current and next mode descriptions', async () => {
  const app = page('dark');
  assert.equal(app.button.dataset.mode, 'dark');
  assert.match(app.button.innerHTML, /<svg/);
  assert.match(app.current.textContent, /深色模式/);
  assert.match(app.next.textContent, /跟随系统/);
  assert.match(app.button.attributes['aria-label'], /当前：深色模式/);
  assert.equal(app.requests.length, 0);
});

test('theme button cycles through all modes and saves the chosen mode', async () => {
  const app = page();
  for (const theme of ['light', 'dark', 'system']) {
    await app.button.click();
    assert.equal(app.root.dataset.theme, theme);
    assert.equal(app.button.dataset.mode, theme);
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
  const pending = app.button.click();
  await Promise.resolve();
  assert.equal(app.root.dataset.theme, 'light');
  assert.equal(app.button.attributes['aria-disabled'], 'true');
  await app.button.click();
  assert.equal(app.requests.length, 1);
  finish({ ok: false, json: async () => ({ detail: 'denied' }) });
  await pending;
  assert.equal(app.root.dataset.theme, 'system');
  assert.equal(app.button.dataset.mode, 'system');
  assert.equal(app.button.attributes['aria-disabled'], 'false');
  assert.match(app.current.textContent, /跟随系统/);
  assert.match(app.status.textContent, /保存失败/);
});

test('login theme changes stay on the page and do not write a signed-out account preference', async () => {
  const app = page('system', false);
  assert.equal(app.button.dataset.mode, 'system');
  await app.button.click();
  assert.equal(app.root.dataset.theme, 'light');
  assert.equal(app.requests.length, 0);
  assert.equal(page('dark').button.dataset.mode, 'dark');
});

test('theme fade runs where supported and skips animation for reduced motion', async () => {
  const app = page('system', true, true);
  await app.button.click();
  assert.equal(app.state.transitions, 1);
  assert.equal(app.root.dataset.theme, 'light');
  app.state.reduced = true;
  await app.button.click();
  assert.equal(app.state.transitions, 1);
  assert.equal(app.root.dataset.theme, 'dark');
});

test('animation failure still switches and saves the preference', async () => {
  const app = page('system', true, true);
  app.document.startViewTransition = () => { throw new Error('Animation unavailable'); };
  await app.button.click();
  assert.equal(app.root.dataset.theme, 'light');
  assert.deepEqual(JSON.parse(app.requests[0].options.body), { theme: 'light' });
  assert.equal(app.status.textContent, '');
});
