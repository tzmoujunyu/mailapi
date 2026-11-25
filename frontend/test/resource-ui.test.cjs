const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../../mailapi_shared/static');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

function page(t) {
  const dom = new JSDOM(read('index.html'), {url:'http://localhost/', runScripts:'outside-only', pretendToBeVisual:true});
  const w = dom.window, d = w.document;
  t.after(() => w.close());
  d.body.classList.add('test-ui-page', 'test-ui-home');
  const rows = d.getElementById('rows');
  rows.innerHTML = '<tr><td><span class="account-email">alice@example.test</span> PRO 可用</td></tr>'
    + '<tr class="detail-row"><td>login details for alice</td></tr>'
    + '<tr><td><span class="account-email">bob@example.test</span> PROLITE 需重新授权</td></tr>';
  d.getElementById('codex-tip-list').innerHTML = '<li class="codex-tip-item">使用提示</li>';
  w.eval(read('test-ui.js'));
  const input = d.querySelector('.resource-search-field input');
  const enter = async value => {
    input.value = value; input.dispatchEvent(new w.Event('input'));
    await new Promise(resolve => setTimeout(resolve, 180));
  };
  return {w, d, rows, input, enter};
}

test('resource counts reuse the directory and repeated enhancement does not duplicate controls', async t => {
  const {w, d, rows} = page(t);
  assert.equal(d.querySelectorAll('.resource-count').length, 4);
  assert.equal(d.querySelector('.home-directory a[href="#chatgpt-accounts"] .resource-count').textContent, '2');
  assert.equal(d.querySelector('.home-directory a[href="#codex-tips"] .resource-count').textContent, '1');
  w.TestUI.enhanceResources(d);
  assert.equal(d.querySelectorAll('.resource-search-tools').length, 1);
  assert.equal(d.querySelector('.test-ui-home-summary'), null);
  rows.insertAdjacentHTML('beforeend', '<tr><td><span class="account-email">carol</span></td></tr>');
  await settle();
  assert.equal(d.querySelector('.resource-count').textContent, '3');
});

test('resource search filters login details with their account and resets without removing rows or controls', async t => {
  const {d, rows, input, enter} = page(t);
  await enter('  ＢＯＢ   proLITE ');
  assert.deepEqual([...rows.children].map(row => row.hidden), [true, true, false]);
  assert.equal(d.querySelector('.resource-search-count').textContent, '1 / 2 账号');
  await enter('alice');
  assert.deepEqual([...rows.children].map(row => row.hidden), [false, false, true]);
  await enter('no-match');
  assert.equal(d.querySelector('.resource-search-empty').hidden, false);
  d.querySelector('.resource-search-tools button').click();
  assert.equal(input.value, '');
  assert.equal(d.activeElement, input);
  assert.equal(d.querySelector('.resource-search-empty').hidden, true);
  assert.equal(rows.children.length, 3);
  assert.ok([...rows.children].every(row => !row.hidden));
});

test('refreshing records reapplies the active resource search and Escape clears it', async t => {
  const {w, d, rows, input, enter} = page(t);
  await enter('alice');
  rows.innerHTML = '<tr><td><span class="account-email">bob@example.test</span></td></tr>';
  await settle();
  assert.equal(rows.firstElementChild.hidden, true);
  assert.equal(d.querySelector('.resource-search-count').textContent, '0 / 1 账号');
  input.dispatchEvent(new w.KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true}));
  assert.equal(rows.firstElementChild.hidden, false);
  assert.equal(input.value, '');
});

test('expiry warnings remain visible while their source and inspection time use an optional disclosure', t => {
  const {w, d} = page(t);
  w.eval(read('account-subscription.js'));
  const container = d.createElement('div');
  container.innerHTML = w.renderAccountSubscription({subscription_expires_on:'2026-11-23'}, value => String(value), new w.Date('2026-11-24T12:00:00'));
  const details = container.querySelector('details');
  assert.equal(details.open, false);
  assert.match(container.querySelector('.subscription-expiry.subscription-due').textContent, /已到期/);
  assert.equal(container.querySelector('.subscription-expiry').closest('details'), null);
  assert.match(details.textContent, /手填到期日/);
  assert.match(details.textContent, /授权上次核对/);
});

test('Escape closes a console disclosure and returns focus to its own trigger', t => {
  const {w, d} = page(t);
  d.body.insertAdjacentHTML('beforeend', '<details class="console-disclosure" open><summary>更多操作</summary><button>修改记录</button></details>');
  const details = d.querySelector('.console-disclosure'), button = details.querySelector('button');
  button.focus();
  button.dispatchEvent(new w.KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true}));
  assert.equal(details.open, false);
  assert.equal(d.activeElement, details.querySelector('summary'));
});
