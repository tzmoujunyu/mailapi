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
    '/api/finance/groups': {items: []},
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
  w.eval(read('test-finance.js') + '\n' + read('test-features.js'));
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
  const current = () => w.document.querySelector('[aria-current="page"]').getAttribute('href');
  assert.equal(current(), '/test-features#view-overview');
  const toggle = w.document.querySelector('button[aria-controls="workspace-navigation"]');
  toggle.click();
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(w.sessionStorage.getItem('mailapi.test.dock'), 'expanded');
  assert.equal(w.document.querySelectorAll('.test-ui-nav a').length, 2);
  w.location.hash = '#view-finance';
  w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  assert.equal(current(), '/test-features#view-finance');
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

test('the initial password reminder follows single-document navigation and appears only once on the overview', async t => {
  const dom = new JSDOM('<body class="test-ui-page"><aside class="test-ui-sidebar"><div class="test-ui-sidebar-footer"></div></aside></body>', {url: 'http://localhost/test-features#view-api2api', runScripts: 'outside-only'});
  const w = dom.window;
  t.after(() => w.close());
  let prompts = 0;
  w.HTMLDialogElement.prototype.showModal = function () {this.open = true; prompts++;};
  w.HTMLDialogElement.prototype.close = function () {this.open = false;};
  w.fetch = async () => ({ok: true, json: async () => ({user: {username: 'alice', role: 'user', must_change_password: true}, groups: []})});
  w.eval(read('session.js'));
  await new Promise(resolve => setImmediate(resolve));
  const dialog = w.document.querySelector('.site-password');
  assert.equal(dialog.open, false);
  w.history.pushState(null, '', '/test-features#view-overview');
  w.dispatchEvent(new w.CustomEvent('test:navigation'));
  assert.equal(dialog.open, true);
  w.history.pushState(null, '', '/');
  w.dispatchEvent(new w.CustomEvent('test:navigation'));
  assert.equal(dialog.open, false);
  w.history.pushState(null, '', '/test-features#view-overview');
  w.dispatchEvent(new w.CustomEvent('test:navigation'));
  assert.equal(prompts, 1);
});

test('appearance drawer persists account settings and restores the saved appearance after a failed save', async t => {
  const dom = new JSDOM('<header class="test-ui-header"><div class="test-ui-header-actions"></div></header>', {url: 'http://localhost/test-features', runScripts: 'outside-only'});
  t.after(() => dom.window.close());
  const w = dom.window;
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  let stored = {theme: 'system', preset: 'default', font: 'auto', radius: 'auto'};
  let fail = false;
  w.fetch = async (url, options = {}) => {
    if (options.method === 'PUT' && !fail) stored = {...stored, ...JSON.parse(options.body)};
    return {ok: !fail, json: async () => stored};
  };
  w.eval(read('test-header.js'));
  await new Promise(resolve => setImmediate(resolve));
  w.document.getElementById('header-appearance').click();
  assert.equal(w.document.querySelector('dialog').open, true);
  const select = async (name, value) => {
    const input = w.document.querySelector(`input[name="${name}"][value="${value}"]`);
    input.checked = true;
    input.dispatchEvent(new w.Event('change', {bubbles: true}));
    await new Promise(resolve => setImmediate(resolve));
  };
  await select('preset', 'lake-view');
  await select('font', 'serif');
  await select('radius', '0.5');
  assert.equal(stored.preset, 'lake-view');
  assert.equal(w.document.documentElement.dataset.uiFont, 'serif');
  assert.equal(w.document.documentElement.style.getPropertyValue('--ui-radius'), '0.5rem');
  fail = true;
  await select('theme', 'dark');
  assert.equal(w.document.documentElement.dataset.theme, 'system');
  assert.match(w.document.querySelector('[role="status"]').textContent, /保存失败/);
});

test('account page layout styles do not match the shared header or its brand at any breakpoint', t => {
  const dom = new JSDOM('<body class="test-ui-page test-ui-access"><header class="test-ui-header"><a class="test-ui-brand-name">MILVLG</a></header></body>');
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  const shared = doc.querySelector('.test-ui-header');
  const brand = doc.querySelector('a');
  for (const name of ['access.css', 'test-ui.css']) {
    const style = doc.createElement('style');
    style.textContent = read(name);
    doc.head.append(style);
    const visit = rules => {
      for (const rule of rules) {
        if (rule.cssRules) visit(rule.cssRules);
        if (!rule.selectorText || !(name === 'access.css' || rule.selectorText.includes('test-ui-access'))) continue;
        const affectsLayout = ['padding','margin','max-width','background','display'].some(key => rule.style.getPropertyValue(key));
        if (!affectsLayout) continue;
        assert.ok(!shared.matches(rule.selectorText), `${name}: ${rule.selectorText} overrides the shared header`);
        assert.ok(!brand.matches(rule.selectorText), `${name}: ${rule.selectorText} overrides the shared brand`);
      }
    };
    visit(style.sheet.cssRules);
  }
});
