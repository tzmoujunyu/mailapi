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
