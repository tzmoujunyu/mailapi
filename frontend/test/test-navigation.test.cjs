const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {JSDOM} = require('jsdom');

const root = path.resolve(__dirname, '../..');
const read = name => fs.readFileSync(path.join(root, 'mailapi_shared/static', name), 'utf8');
const pages = JSON.parse(execFileSync('uv', ['run', '--offline', '--python', '.venv/bin/python', 'python', '-c', `
import json
from pathlib import Path
from mailapi_shared.test_navigation import PAGES, prepare_workspace_page, page_controller
p = Path('mailapi_shared/static')
data = {name: {'html': prepare_workspace_page((p / file).read_text(), name), 'code': page_controller(p, name)} for name, file in PAGES.items()}
data['editor'] = {'code': page_controller(p, 'editor')}
print(json.dumps(data))
`], {cwd: root, env: {...process.env, UV_CACHE_DIR: '/private/tmp/mailapi-uv-cache'}, encoding: 'utf8'}));
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

function fixture(t, {initial = 'features', real = false, previewUser = ''} = {}) {
  const initialPath = previewUser ? '/preview?user_id=' + previewUser
    : {features: '/test-features', home: '/', access: '/admin/access', admin: '/admin'}[initial];
  const dom = new JSDOM(real ? pages[initial].html : `<html><head><style data-test-page-style="${initial}"></style></head><body><div class="test-page-view" data-test-view="${initial}"><input id="draft"><div class="management-search"><input id="search"></div></div></body></html>`, {
    url: 'http://localhost' + initialPath, runScripts: 'outside-only', pretendToBeVisual: true,
  });
  const w = dom.window;
  t.after(() => w.close());
  w.document.body.classList.add('test-ui-page', 'test-ui-' + initial);
  w.document.querySelector('[data-test-view]').dataset.testAccountId = 'admin';
  if (previewUser) w.document.querySelector('[data-test-view]').dataset.testPreviewUser = previewUser;
  const sidebar = w.document.createElement('aside');
  sidebar.className = 'test-ui-sidebar';
  sidebar.innerHTML = '<nav class="test-ui-nav">' + Object.entries({
    '/test-features#view-overview': '主页', '/': '资源', '/admin/access': '账户', '/admin': '控制台',
    '/test-features#view-api2api': 'API', '/test-features#view-finance': '财务',
  }).map(([href, label]) => `<a href="${href}" aria-label="${label}"><span></span><span>${label}</span></a>`).join('') + '</nav><div class="test-ui-sidebar-footer"></div>';
  w.document.body.prepend(sidebar);
  w.scrollTo = () => {};
  w.Element.prototype.scrollIntoView = () => {};
  w.matchMedia = () => ({matches: false, addEventListener() {}});
  w.CSS = {escape: value => value};
  w.ResizeObserver = class {observe() {} disconnect() {}};
  w.HTMLDialogElement.prototype.showModal = function () {this.open = true;};
  w.HTMLDialogElement.prototype.close = function () {this.open = false; this.dispatchEvent(new w.Event('close'));};
  const originalInsert = w.document.head.insertBefore.bind(w.document.head);
  w.document.head.insertBefore = (node, before) => {
    const result = originalInsert(node, before);
    if (node.tagName === 'LINK') queueMicrotask(() => node.dispatchEvent(new w.Event('load')));
    return result;
  };
  const intervals = [];
  w.setInterval = (callback, delay) => {intervals.push({callback, delay}); return intervals.length;};
  w.clearInterval = () => {};
  const calls = [], mounts = new Map(), contexts = new Map();
  let blocked = false, fail = false, slow;
  const apiData = url => {
    if (url === '/api/auth/me') return {user: {id: 'admin', username: 'admin', role: blocked ? 'user' : 'admin'}, groups: []};
    if (url === '/api/test-features/status') return {role: 'admin'};
    if (url === '/api/admin/users') return {items: [{id: 'alice', username: 'alice', role: 'user'}]};
    if (url.includes('/usage')) return {items: [], requests: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0};
    if (url.includes('/config')) return {};
    return {items: [], server_time: Date.now() / 1000};
  };
  w.fetch = async input => {
    const url = String(input);
    calls.push(url);
    if (!url.startsWith('/api/')) {
      if (url === '/admin/access' && slow) await slow;
      const target = new URL(url, w.location);
      const name = {'/': 'home', '/preview': 'home', '/admin/access': 'access', '/admin': 'admin', '/test-features': 'features'}[target.pathname];
      const preview = target.pathname === '/preview' ? ` data-test-preview-user="${target.searchParams.get('user_id') || 'admin'}"` : '';
      return {ok: !fail, url: 'http://localhost' + url, text: async () => real ? pages[name].html.replace('data-test-view="' + name + '"', 'data-test-view="' + name + '" data-test-account-id="admin"' + preview)
        : `<title>${name}</title><style data-test-page-style="${name}">.sample {color:red}</style><div class="test-page-view" data-test-view="${name}" data-test-account-id="admin"${preview}><input id="draft"><div class="management-search"><input id="search"></div></div>`};
    }
    return {ok: true, json: async () => apiData(url)};
  };
  w.eval(read('test-ui.js'));
  w.eval(read('test-router.js').replace('export function ', 'function ').replace(/createWorkspaceRouter\(\);\s*$/, ''));
  const router = w.createWorkspaceRouter({loadController: async name => ({mount(context) {
    contexts.set(name, context);
    mounts.set(name, (mounts.get(name) || 0) + 1);
    if (real) {
      w.eval(pages[name].code.replace('export function ', 'function ') + ';window.fixtureMount = mount;');
      w.fixtureMount(context);
    } else {
      context.setInterval(() => {context.document.querySelector('#draft').dataset.tick = 'yes';}, 1000);
      context.document.querySelector('#draft').dataset.page = name;
    }
  }})});
  return {w, router, calls, mounts, contexts, intervals, block: value => {blocked = value;}, fail: value => {fail = value;}, delay: value => {slow = value;}};
}

test('all sidebar routes keep the same shell and cached page state, with one controller per page', async t => {
  const f = fixture(t);
  const {w, router, mounts, contexts, intervals} = f;
  await router.ready;
  const header = w.document.querySelector('.test-ui-header');
  const sidebar = w.document.querySelector('.test-ui-sidebar');
  header.querySelector('.test-ui-dock-toggle').click();
  const first = w.document.querySelector('[data-test-view]');
  first.querySelector('#draft').value = 'unsaved';
  for (const url of ['/', '/admin/access', '/admin', '/test-features#view-api2api', '/test-features#view-finance', '/test-features#view-overview']) {
    await router.navigate(url);
    assert.equal(w.document.querySelector('.test-ui-header'), header);
    assert.equal(w.document.querySelector('.test-ui-sidebar'), sidebar);
    assert.equal(header.querySelector('.test-ui-dock-toggle').getAttribute('aria-expanded'), 'true');
    assert.equal(w.document.querySelectorAll('[data-test-view]').length, 1);
    assert.equal(w.document.querySelector('[aria-current="page"]').href, new URL(url, w.location).href);
  }
  assert.equal(w.document.querySelector('[data-test-view]'), first);
  assert.equal(first.querySelector('#draft').value, 'unsaved');
  assert.deepEqual([...mounts.values()], [1, 1, 1, 1]);
  assert.equal(contexts.get('access').document.querySelector('#draft').dataset.page, 'access');
  intervals.forEach(timer => timer.callback());
  assert.equal(first.querySelector('#draft').dataset.tick, 'yes');
  assert.equal(contexts.get('home').document.querySelector('#draft').dataset.tick, undefined);
  assert.equal(contexts.get('access').document.hidden, true);
  assert.equal(w.document.querySelector('.test-ui-header-search #search').isConnected, true);
  assert.equal(w.document.querySelector('[data-test-page-style="access"]').media, 'not all');
  assert.equal(w.document.querySelector('[data-test-page-style="features"]').media, '');
});

test('rapid navigation, failed requests, and revoked permissions cannot replace the current view', async t => {
  const f = fixture(t);
  await f.router.ready;
  let release;
  f.delay(new Promise(resolve => {release = resolve;}));
  const older = f.router.navigate('/admin/access');
  await settle();
  await f.router.navigate('/admin');
  release();
  await older;
  assert.equal(f.w.location.pathname, '/admin');
  assert.equal(f.mounts.has('access'), false);
  f.fail(true);
  await f.router.navigate('/');
  assert.equal(f.w.document.querySelector('[data-test-view]').dataset.testView, 'admin');
  f.fail(false);
  await f.router.navigate('/admin/access');
  await f.router.navigate('/');
  f.block(true);
  await f.router.navigate('/admin/access');
  assert.equal(f.w.location.pathname, '/');
  assert.equal(f.w.document.querySelector('[data-test-view]').dataset.testView, 'home');
  assert.equal(f.mounts.get('access'), 1);
});

test('browser back and forward restore content without remounting or rebuilding navigation', async t => {
  const {w, router, mounts} = fixture(t);
  await router.ready;
  await router.navigate('/admin/access');
  await router.navigate('/admin');
  const change = () => new Promise(resolve => w.addEventListener('test:navigation', resolve, {once: true}));
  let navigated = change();
  w.history.back();
  await navigated;
  assert.equal(w.document.querySelector('[data-test-view]').dataset.testView, 'access');
  navigated = change();
  w.history.forward();
  await navigated;
  assert.equal(w.document.querySelector('[data-test-view]').dataset.testView, 'admin');
  assert.equal(mounts.get('access'), 1);
  assert.equal(mounts.get('admin'), 1);
});

test('draft leave guard keeps the current URL and page until the user agrees to leave', async t => {
  const {w, router, contexts, mounts} = fixture(t);
  await router.ready;
  await router.navigate('/test-features#view-finance');
  const current = w.location.href;
  let allow = false, checks = 0;
  contexts.get('features').window.FinanceDrafts = {async canLeave() {checks++; return allow;}};
  await router.navigate('/test-features#view-api2api');
  await router.navigate('/admin/access');
  assert.equal(w.location.href, current);
  assert.equal(mounts.has('access'), false);
  w.history.replaceState(null, '', '/');
  await router.navigate('/', {push: false});
  assert.equal(w.location.href, current);
  assert.equal(w.document.querySelector('[data-test-view]').dataset.testView, 'features');
  allow = true;
  await router.navigate('/admin/access');
  assert.equal(w.location.pathname, '/admin/access');
  assert.equal(checks, 4);
});

test('actual resources, accounts, console, API and finance controllers work together in the persistent shell', async t => {
  const {w, router, mounts, calls} = fixture(t, {real: true});
  await router.ready;
  await settle();
  for (const [url, expected] of [['/admin/access', 'access'], ['/admin', 'admin'], ['/', 'home'], ['/test-features#view-finance', 'features'], ['/test-features#view-api2api', 'features']]) {
    await router.navigate(url);
    await settle();
    assert.equal(w.document.querySelector('[data-test-view]').dataset.testView, expected);
    assert.equal(w.document.querySelector('.test-navigation-status').hidden, true);
    assert.equal(w.document.querySelectorAll('.test-ui-header').length, 1);
    if (expected === 'features') assert.equal(w.document.querySelector('.workspace-view.active').dataset.view, url.split('#view-')[1]);
  }
  assert.equal(mounts.size, 4);
  assert.ok(calls.includes('/api/admin/users'));
  assert.ok(calls.includes('/api/accounts'));
  assert.ok(calls.includes('/api/finance/applications'));
  assert.ok(!calls.some(url => url.includes('gpt-password') || url.includes('totp-code')));
  assert.ok(!w.document.getElementById('api-key-status').textContent);
});

test('shared reimbursement notifications reach the mounted finance view and page changes refresh the header', async t => {
  const {w,router,calls,contexts}=fixture(t,{real:true});
  await router.ready;
  await router.navigate('/test-features#view-finance');await settle();
  w.document.dispatchEvent(new w.CustomEvent('finance:open-application',{detail:{id:'notice-app'}}));
  await settle();await settle();
  assert.ok(calls.includes('/api/finance/applications/notice-app/history'));
  assert.equal(w.document.getElementById('fin-detail-dialog').open,true);
  w.document.getElementById('fin-detail-dialog').close();
  let refreshed=0;
  w.document.addEventListener('finance:notifications-changed',()=>refreshed++);
  contexts.get('features').document.dispatchEvent(new w.CustomEvent('finance:notifications-changed'));
  assert.equal(refreshed,1);
  await router.navigate('/');
  const before=calls.filter(url=>url.endsWith('/notice-app/history')).length;
  w.document.dispatchEvent(new w.CustomEvent('finance:open-application',{detail:{id:'notice-app'}}));await settle();
  assert.equal(calls.filter(url=>url.endsWith('/notice-app/history')).length,before);
});

test('the lazy real editor keeps its console scope and is reused after leaving and returning', async t => {
  const {w, router, mounts} = fixture(t, {real: true});
  await router.ready;
  await router.navigate('/admin');
  await settle();
  w.document.getElementById('codex-tips-tab').click();
  w.document.getElementById('add-codex-tip').click();
  await settle();
  const vendor = w.document.querySelector('script[src="/static/vendor/tiptap/tiptap.js"]');
  assert.ok(vendor);
  w.eval(read('vendor/tiptap/tiptap.js'));
  const RealEditor = w.MailapiTiptap.Editor;
  let editor;
  w.MailapiTiptap.Editor = class extends RealEditor {constructor(options) {super(options); editor = this;}};
  t.after(() => editor?.destroy());
  vendor.dispatchEvent(new w.Event('load'));
  await settle();
  assert.equal(w.document.querySelectorAll('.ProseMirror').length, 1);
  assert.equal(w.document.getElementById('codex-tip-save').disabled, false);
  w.document.getElementById('codex-tip-cancel').click();
  await router.navigate('/admin/access');
  await router.navigate('/admin');
  await settle();
  w.document.getElementById('add-codex-tip').click();
  await settle();
  assert.equal(w.document.querySelectorAll('.ProseMirror').length, 1);
  assert.equal(mounts.get('editor'), 1);
  assert.equal(w.document.getElementById('codex-tip-save').disabled, false);
});

test('the account preview link uses the new shell and isolates each selected user from ordinary resources', async t => {
  const {w, router, contexts, calls} = fixture(t, {real: true, initial: 'access'});
  await router.ready;
  await settle();
  const header = w.document.querySelector('.test-ui-header');
  const link = w.document.querySelector('#users a[href="/preview?user_id=alice"]');
  assert.ok(link);
  const entered = new Promise(resolve => w.addEventListener('test:navigation', resolve, {once: true}));
  link.click();
  await entered;
  await settle();
  const alice = w.document.querySelector('[data-test-view]');
  assert.equal(w.document.querySelector('.test-ui-header'), header);
  assert.equal(alice.dataset.testPreviewUser, 'alice');
  assert.equal(w.document.querySelector('[aria-current="page"]').getAttribute('href'), '/');
  assert.ok(alice.querySelector('.test-ui-home-summary'));
  assert.equal(contexts.get('home').window.__USER_PREVIEW__, true);
  assert.ok(calls.includes('/api/accounts?preview=1&user_id=alice'));
  assert.ok(calls.includes('/api/coding-plans?preview=1&user_id=alice'));
  assert.ok(calls.includes('/api/relay-accounts?preview=1&user_id=alice'));
  await router.navigate('/preview?user_id=bob');
  await settle();
  assert.notEqual(w.document.querySelector('[data-test-view]'), alice);
  assert.equal(contexts.get('home').window.__PREVIEW_USER_ID__, 'bob');
  assert.ok(calls.includes('/api/accounts?preview=1&user_id=bob'));
  // A legacy global must never leak a preview scope into the ordinary page.
  w.__USER_PREVIEW__ = true;
  w.__PREVIEW_USER_ID__ = 'bob';
  await router.navigate('/');
  await settle();
  assert.equal(contexts.get('home').window.__USER_PREVIEW__, false);
  assert.equal(contexts.get('home').window.__PREVIEW_USER_ID__, '');
  assert.ok(calls.includes('/api/accounts'));
  await router.navigate('/preview?user_id=alice');
  await settle();
  assert.equal(w.document.querySelector('[data-test-view]'), alice);
  assert.equal(w.document.querySelector('.test-ui-header'), header);
});

test('direct preview entry mounts the new resource controller with the selected user scope', async t => {
  const {w, router, calls, contexts} = fixture(t, {real: true, initial: 'home', previewUser: 'alice'});
  await router.ready;
  await settle();
  assert.equal(contexts.get('home').window.__PREVIEW_USER_ID__, 'alice');
  assert.ok(calls.includes('/api/accounts?preview=1&user_id=alice'));
  assert.ok(!calls.includes('/api/accounts'));
  const preview = w.document.querySelector('[data-test-view]');
  await router.navigate('/');
  await router.navigate('/preview?user_id=alice');
  assert.equal(w.document.querySelector('[data-test-view]'), preview);
});
