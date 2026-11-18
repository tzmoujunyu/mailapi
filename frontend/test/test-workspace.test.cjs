const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const assets = path.resolve(__dirname, '../../mailapi_shared/static');
const read = name => fs.readFileSync(path.join(assets, name), 'utf8');

test('the overview uses own applications and enabled keys, and links switch the visible view', async t => {
  const dom = new JSDOM(read('test-features.html'), {url: 'http://localhost/test-features', runScripts: 'outside-only'});
  const w = dom.window;
  t.after(() => w.close());
  w.scrollTo = () => {};
  const fixtures = {
    '/api/test-features/status': {role: 'user'},
    '/api/api2api/keys': {items: [{id: 'one', name: 'One', enabled: true}, {id: 'old', name: 'Old', enabled: false}]},
    '/api/api2api/usage': {requests: 2, prompt_tokens: 15, completion_tokens: 5, total_tokens: 20, items: []},
    '/api/finance/announcements': {items: []},
    '/api/finance/applications': {items: [
      {id: '1', project: '待处理', amount: '3.00', status: 'pending'},
      {id: '2', project: '已批准', amount: '6.80', status: 'approved'},
    ]},
  };
  const requests = [];
  w.fetch = async url => {
    requests.push(url);
    return {ok: true, json: async () => fixtures[url]};
  };
  w.eval(read('test-features.js'));
  w.eval(w.document.scripts[w.document.scripts.length - 1].textContent);
  await new Promise(resolve => setImmediate(resolve));
  const el = id => w.document.getElementById(id);
  assert.equal(el('dashboard-keys').textContent, '1');
  assert.equal(el('dashboard-pending').textContent, '1');
  assert.equal(el('finance-approved').textContent, '6.80 元');
  assert.equal(el('usage-ring').style.getPropertyValue('--input-share'), '75%');
  assert.equal(el('dashboard-tokens').textContent, '20');
  assert.ok(el('dashboard-review-link').classList.contains('hidden'));
  assert.ok(requests.every(url => !url.includes('/admin/')));
  for (const view of ['finance', 'api2api']) {
    const changed = new Promise(resolve => w.addEventListener('hashchange', resolve, {once: true}));
    w.document.querySelector(`.overview-grid a[href="#view-${view}"]`).click();
    await changed;
    assert.equal(w.document.querySelector('.workspace-view.active').dataset.view, view);
  }
});

test('the dock enhances the supplied role links and follows URL changes', t => {
  const dom = new JSDOM(`<aside class="test-ui-sidebar"><div class="test-ui-logo">M</div><nav class="test-ui-nav">
    <a href="/test-features#view-overview"><span></span><span>主页</span></a>
    <a href="/test-features#view-finance"><span></span><span>财务</span></a>
  </nav><div class="test-ui-sidebar-footer"></div></aside>`, {url: 'http://localhost/test-features', runScripts: 'outside-only'});
  t.after(() => dom.window.close());
  const w = dom.window;
  w.eval(read('test-ui.js'));
  const current = () => w.document.querySelector('[aria-current="page"]').textContent;
  assert.equal(current(), '主页');
  const toggle = w.document.querySelector('button[aria-controls="workspace-navigation"]');
  toggle.click();
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(w.sessionStorage.getItem('mailapi.test.dock'), 'expanded');
  assert.equal(w.document.querySelectorAll('.test-ui-nav a').length, 2);
  w.location.hash = '#view-finance';
  w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  assert.equal(current(), '财务');
});

test('workspace resources use permission-filtered endpoints and fetch secrets only on a click', async t => {
  const dom = new JSDOM(read('test-features.html'), {url: 'http://localhost/test-features', runScripts: 'outside-only'});
  const w = dom.window;
  t.after(() => w.close());
  const calls = [], copied = [];
  Object.defineProperty(w.navigator, 'clipboard', {value: {writeText: async value => copied.push(value)}});
  const fixtures = {
    '/api/auth/me': {user: {role: 'admin'}},
    '/api/accounts': {items: [{login: 'long+<example>@example.com', has_gpt_password: true, gpt_password_account: 'email:test', has_totp_secret: true, totp_secret_account: 'email:test', codex: []}]},
    '/api/coding-plans': {items: [{id: 'plan', name: 'GLM', provider: 'glm'}]},
    '/api/relay-accounts': {items: [{id: 'relay', name: 'Relay', has_password: true}]},
    '/api/accounts/email%3Atest/gpt-password': {password: 'secret-fixture'},
    '/api/accounts/email%3Atest/totp-code': {code: '123456'},
  };
  w.fetch = async (url, options = {}) => { calls.push([url, options]); return {ok: true, json: async () => fixtures[url]}; };
  w.eval(read('account-subscription.js'));
  w.eval(read('test-workspace.js'));
  await new Promise(resolve => setImmediate(resolve));
  const byId = id => w.document.getElementById(id);
  assert.equal(byId('workspace-asset-count').textContent, '3');
  assert.equal(byId('workspace-resource-link').getAttribute('href'), '/admin');
  assert.ok(!calls.some(([, options]) => options.method === 'POST'));
  assert.equal(w.document.querySelector('.tree-account-name').textContent, 'long+<example>@example.com');
  w.document.querySelector('[data-workspace-action="password"]').click();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(copied, ['secret-fixture']);
  assert.equal(calls.at(-1)[0], '/api/accounts/email%3Atest/gpt-password');
  assert.ok(!w.document.body.textContent.includes('secret-fixture'));
  byId('overview-search').value = 'GLM';
  byId('overview-search').dispatchEvent(new w.Event('input'));
  assert.equal(byId('workspace-gpt-items').firstElementChild.hidden, true);
  assert.equal(byId('workspace-plans-items').firstElementChild.hidden, false);
  byId('overview-search').value = '';
  byId('overview-search').dispatchEvent(new w.Event('input'));
  assert.equal(byId('workspace-gpt-items').firstElementChild.hidden, false);
});

test('test avatar opens account actions and the password dialog while production retains its session bar', async t => {
  for (const testUi of [true, false]) {
    const dom = new JSDOM(`<html data-theme="system"><body class="${testUi ? 'test-ui-page' : ''}"><aside class="test-ui-sidebar"><div class="test-ui-sidebar-footer"></div></aside></body></html>`, {url: 'http://localhost/test-features#view-api2api', runScripts: 'outside-only'});
    const w = dom.window;
    t.after(() => w.close());
    w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    w.HTMLDialogElement.prototype.close = function () { this.open = false; };
    w.SiteTheme = {mount: () => {}};
    w.fetch = async () => ({ok: true, json: async () => ({user: {username: 'alice', role: 'user', must_change_password: true}, groups: []})});
    w.eval(read('session.js'));
    await new Promise(resolve => setImmediate(resolve));
    const dialog = w.document.querySelector('dialog');
    assert.equal(dialog.open, false);
    if (testUi) {
      assert.equal(w.document.querySelector('.site-session'), null);
      const avatar = w.document.querySelector('.test-ui-avatar-button');
      assert.equal(avatar.firstElementChild.textContent, 'AL');
      avatar.click();
      assert.equal(avatar.getAttribute('aria-expanded'), 'true');
      w.document.querySelector('[data-password]').click();
      assert.equal(dialog.open, true);
      assert.equal(avatar.getAttribute('aria-expanded'), 'false');
      dialog.close();
      avatar.click();
      avatar.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
      assert.equal(avatar.getAttribute('aria-expanded'), 'false');
    } else assert.ok(w.document.querySelector('.site-session'));
  }
});
