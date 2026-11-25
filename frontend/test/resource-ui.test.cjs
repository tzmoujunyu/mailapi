const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../../mailapi_shared/static');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

function page(t, preferences) {
  const dom = new JSDOM(read('index.html'), {url:'http://localhost/', runScripts:'outside-only', pretendToBeVisual:true});
  const w = dom.window, d = w.document;
  t.after(() => w.close());
  d.body.classList.add('test-ui-page', 'test-ui-home');
  const rows = d.getElementById('rows');
  rows.innerHTML = '<tr><td><span class="account-email">alice@example.test</span> PRO 可用</td></tr>'
    + '<tr class="detail-row"><td>login details for alice</td></tr>'
    + '<tr><td><span class="account-email">bob@example.test</span> PROLITE 需重新授权</td></tr>';
  d.getElementById('codex-tip-list').innerHTML = '<li class="codex-tip-item">使用提示</li>';
  w.ViewPreferences = preferences;
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
  assert.equal(d.querySelectorAll('.resource-search-tools').length, 3);
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

test('resource status filters use structured fields and combine with normalized keywords', async t => {
  const {w, d, rows, enter} = page(t);
  const alice = rows.children[0], bob = rows.children[2];
  alice.dataset.resourceFlags = 'available'; alice.dataset.resourceRemaining = '10';
  alice.dataset.resourceExpiry = String(Date.now() + 3 * 86400000);
  alice.dataset.resourceSearch = 'alternate-mail@example.test';
  bob.dataset.resourceFlags = 'reauth exhausted'; bob.dataset.resourceExpiry = String(Date.now() - 86400000);
  const state = d.getElementById('resource-status-filter');
  const visible = () => [...rows.querySelectorAll('tr:not(.detail-row)')].filter(row => !row.hidden);
  for (const value of ['available', 'expiring', 'low-quota']) {
    state.value = value; state.dispatchEvent(new w.Event('change'));
    assert.deepEqual(visible(), [alice]);
    assert.equal(alice.nextElementSibling.hidden, false);
  }
  await enter('alternate-mail');
  assert.deepEqual(visible(), [alice]);
  state.value = 'reauth'; state.dispatchEvent(new w.Event('change'));
  assert.deepEqual(visible(), []);
  await enter('');
  for (const value of ['reauth', 'expired', 'exhausted']) {
    state.value = value; state.dispatchEvent(new w.Event('change'));
    assert.deepEqual(visible(), [bob]);
  }
  // Translated labels must not classify a record whose structured state is unknown.
  bob.dataset.resourceFlags = ''; state.dispatchEvent(new w.Event('change'));
  assert.deepEqual(visible(), []);
  d.getElementById('resource-filter-reset').click();
  assert.equal(state.value, 'all'); assert.equal(visible().length, 2);
});

test('resource sort keeps account details together, unknown values last and restores original order', async t => {
  const {w, d, rows} = page(t);
  rows.innerHTML = '<tr data-resource-order="0" data-resource-remaining="5" data-resource-expiry="300"><td><span class="account-email">first</span></td></tr>'
    + '<tr class="detail-row"><td><button>first login</button></td></tr>'
    + '<tr data-resource-order="1"><td><span class="account-email">unknown</span></td></tr>'
    + '<tr data-resource-order="2" data-resource-remaining="80" data-resource-expiry="100"><td><span class="account-email">last</span></td></tr>';
  await settle();
  const detail = rows.querySelector('.detail-row'), focused = detail.querySelector('button');
  const sort = d.getElementById('resource-sort-order');
  const order = () => [...rows.querySelectorAll('.account-email')].map(node => node.textContent);
  focused.focus();
  sort.value = 'quota'; sort.dispatchEvent(new w.Event('change'));
  assert.deepEqual(order(), ['last', 'first', 'unknown']);
  assert.equal(detail.previousElementSibling.textContent, 'first');
  assert.equal(d.activeElement, focused);
  await settle();
  const mutations = [];
  const observer = new w.MutationObserver(records => mutations.push(...records));
  observer.observe(rows, {childList: true});
  await settle(); await settle();
  assert.equal(mutations.length, 0);
  sort.value = 'expiry'; sort.dispatchEvent(new w.Event('change'));
  assert.deepEqual(order(), ['last', 'first', 'unknown']);
  sort.value = 'original'; sort.dispatchEvent(new w.Event('change'));
  assert.deepEqual(order(), ['first', 'unknown', 'last']);
  observer.disconnect();
});

test('newly refreshed records retain active filters and sorting', async t => {
  const {w, d, rows} = page(t);
  const state = d.getElementById('resource-status-filter'), sort = d.getElementById('resource-sort-order');
  state.value = 'available'; sort.value = 'quota'; sort.dispatchEvent(new w.Event('change'));
  rows.innerHTML = '<tr data-resource-order="0" data-resource-flags="available" data-resource-remaining="40"><td><span class="account-email">low</span></td></tr>'
    + '<tr data-resource-order="1" data-resource-flags="available" data-resource-remaining="90"><td><span class="account-email">high</span></td></tr>'
    + '<tr data-resource-order="2" data-resource-flags="reauth"><td><span class="account-email">reauth</span></td></tr>';
  await settle();
  assert.equal(rows.firstElementChild.textContent, 'high');
  assert.equal(rows.lastElementChild.hidden, true);
  assert.equal(state.value, 'available'); assert.equal(sort.value, 'quota');
});

test('Coding Plan quota filters and relay keyword search stay scoped to their own tables', async t => {
  const {w, d, rows} = page(t);
  const plans = d.getElementById('coding-plan-rows'), relays = d.getElementById('relay-account-rows');
  plans.innerHTML = '<tr data-resource-order="0" data-resource-remaining="0" data-resource-flags="exhausted"><td><span class="resource-name">empty</span></td></tr>'
    + '<tr data-resource-order="1" data-resource-remaining="8"><td><span class="resource-name">low</span></td></tr>'
    + '<tr data-resource-order="2"><td><span class="resource-name">unknown</span></td></tr>';
  relays.innerHTML = '<tr><td><span class="resource-name">first relay</span></td></tr><tr><td><span class="resource-name">second relay</span></td></tr>';
  await settle();
  const state = d.getElementById('coding-plan-status-filter');
  state.value = 'low-quota'; state.dispatchEvent(new w.Event('change'));
  assert.deepEqual([...plans.children].map(row => row.hidden), [true, false, true]);
  state.value = 'exhausted'; state.dispatchEvent(new w.Event('change'));
  assert.deepEqual([...plans.children].map(row => row.hidden), [false, true, true]);
  const search = d.getElementById('relay-search-input');
  search.value = 'SECOND'; search.dispatchEvent(new w.Event('input'));
  await new Promise(resolve => setTimeout(resolve, 180));
  assert.deepEqual([...relays.children].map(row => row.hidden), [true, false]);
  assert.ok([...rows.children].every(row => !row.hidden));
  assert.equal(d.getElementById('relay-status-filter'), null);
  assert.equal(d.querySelector('#coding-plan-status-filter option[value="expiring"]'), null);
});

test('resource preferences restore once and save only status/sort after user interaction', async t => {
  let reads = 0;
  const writes = [];
  const {w, d, rows, enter} = page(t, {
    read: async () => { reads++; return {gptStatus: 'available', gptSort: 'quota', codingStatus: 'low-quota', codingSort: 'original'}; },
    save: (name, settings) => writes.push([name, {...settings}]),
  });
  await settle();
  assert.equal(d.getElementById('resource-status-filter').value, 'available');
  assert.equal(d.getElementById('resource-sort-order').value, 'quota');
  assert.equal(d.getElementById('coding-plan-status-filter').value, 'low-quota');
  assert.equal(writes.length, 0);
  await enter('private search term');
  assert.equal(writes.length, 0);
  d.getElementById('resource-status-filter').value = 'reauth';
  d.getElementById('resource-status-filter').dispatchEvent(new w.Event('change'));
  assert.deepEqual(writes, [['resources', {gptStatus: 'reauth', gptSort: 'quota'}]]);
  rows.innerHTML = '<tr><td><span class="account-email">replacement</span></td></tr>';
  await settle(); w.TestUI.enhanceResources(d);
  assert.equal(reads, 1);
  assert.equal(d.getElementById('resource-status-filter').value, 'reauth');
});

test('late preference loading does not replace a user filter selection', async t => {
  let resolve;
  const {w, d} = page(t, {read: () => new Promise(done => { resolve = done; }), save: () => {}});
  const state = d.getElementById('resource-status-filter');
  state.value = 'expired'; state.dispatchEvent(new w.Event('change'));
  resolve({gptStatus: 'available', gptSort: 'quota'});
  await settle();
  assert.equal(state.value, 'expired');
  assert.equal(d.getElementById('resource-sort-order').value, 'original');
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
