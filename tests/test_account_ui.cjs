const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const fixture = () => [{
  key: 'email:user@example.com', email: 'user@example.com', login: 'user@example.com', revision: 'rev-1',
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
const mailboxFixture = () => [{ name: 'account-1', resource_key: 'mailbox:one', email: 'user@example.com', provider: 'gmail', listed: true,
  note: '硅基流动登录码 <img src=x onerror=alert(1)>',
  enabled: true, result_type: 'code', result_value: '321321', rule_name: 'SiliconFlow', subject: 'All-rule code' },
{ name: 'account-2', resource_key: 'mailbox:two', email: 'mail-only@example.com', provider: 'outlook', listed: false,
  enabled: false, result_type: 'link', result_value: 'https://claude.ai/login#mail-only', rule_name: 'Claude', subject: 'Claude login' }];
function page(file, preview = false, previewUserId = '') {
  const nodes = new Map();
  const calls = [];
  const timers = new Map();
  let counter = 0;
  const element = () => ({
    innerHTML: '', textContent: '', dataset: {}, value: '', listeners: {},
    addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    showModal() { this.open = true; }, close() { this.open = false; }, focus() {}, reset() {},
  });
  const document = { ...element(), getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
    createElement() { const node = element(); Object.defineProperty(node, 'textContent', { set(text) { this.innerHTML = String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); } }); return node; },
  };
  const context = vm.createContext({ document, console, Date, Map, Set, Promise, AbortSignal, URLSearchParams,
    performance: { now: () => Date.now() },
    setInterval(callback) { timers.set(++counter, callback); return counter; }, clearInterval(id) { timers.delete(id); },
    setTimeout(callback) { return ++counter; },
    navigator: { clipboard: { async writeText(value) { calls.push(['clipboard', value]); } } }, window: { isSecureContext: true, __USER_PREVIEW__: preview, __PREVIEW_USER_ID__: previewUserId },
    confirm: () => true,
    fetch: async (url, options) => { calls.push([url, options]); return { ok: true, json: async () => ({ items: url.includes('history') ? [{ code: '654321', subject: 'Historical code' }] : url.includes('/api/mailboxes') || url.includes('/api/admin/mail/accounts') ? mailboxFixture() : fixture(), password: 'password-value', code: '081804' }) }; },
  });
  const sourceFile = process.env.MAILAPI_UI_ROOT ? file.replace(/^static\//, `${process.env.MAILAPI_UI_ROOT}/`) : file;
  const quotaScript = sourceFile.replace(/[^/]+$/, 'coding-plan-quota.js');
  vm.runInContext(fs.readFileSync(quotaScript, 'utf8'), context);
  for (const name of ['coding-plan-config.js', 'account-subscription.js']) {
    vm.runInContext(fs.readFileSync(sourceFile.replace(/[^/]+$/, name), 'utf8'), context);
  }
  vm.runInContext(fs.readFileSync(sourceFile, 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1], context);
  if (file === 'static/index.html') vm.runInContext(fs.readFileSync(sourceFile.replace(/[^/]+$/, 'mailbox-resources.js'), 'utf8'), context);
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

test('subscription display warns at five days and expires without assuming renewal', async () => {
  const app = page('static/index.html'); await flush();
  const render = date => vm.runInContext(`renderAccountSubscription({subscription_started_on:'2026-01-31',subscription_ends_on:'2026-02-28'},escapeHtml,new Date(2026,1,${date},12))`, app.context);
  assert.match(render(22), /26\.01\.31 \(6 Day left\)/);
  assert.doesNotMatch(render(22), /subscription-due/);
  assert.match(render(23), /subscription-due/);
  assert.match(render(23), /5 Day left/);
  assert.match(render(28), /已到期/);
  assert.match(vm.runInContext('renderAccountSubscription({},escapeHtml)', app.context), /未设置/);
});

test('homepage member edits shared fields without sending blank secrets or a mailbox change', async () => {
  const app = page('static/index.html'); await flush();
  await app.click('rows', '[data-edit-gpt-key]', { editGptKey: 'email:user@example.com' });
  assert.equal(app.nodes.get('gpt-edit-dialog').open, true);
  assert.equal(app.nodes.get('gpt-edit-login').value, 'user@example.com');
  app.nodes.get('gpt-edit-expiry').value = '2027-03-02';
  app.nodes.get('gpt-edit-form').listeners.submit[0]({ preventDefault() {} });
  await flush();
  const [, request] = app.calls.find(([url]) => url.endsWith('/info'));
  assert.deepEqual(JSON.parse(request.body), { subscription_expires_on: '2027-03-02', revision: 'rev-1' });
  assert.equal(app.nodes.get('gpt-edit-dialog').open, false);
  const preview = page('static/index.html', true); await flush();
  assert.doesNotMatch(preview.nodes.get('rows').innerHTML, /data-edit-gpt-key/);
});

test('console history escapes content, searches by tag and sends the selected review', async () => {
  const app = page('static/admin.html'); await flush();
  app.context.fetch = async (url, options) => {
    app.calls.push([url, options]);
    return { ok: true, json: async () => ({ total: 1, items: [{ id: 'event-1', source: 'user',
      actor: 'reader', at: '2026-11-09T00:00:00Z', descriptions: ['更新了 <img src=x>'],
      restorable_fields: [{ field: 'secret', label: '2FA 密钥' }] }] }) };
  };
  await app.click('account-rows', '[data-gpt-changes]', { gptChanges: 'email:user@example.com' });
  assert.match(app.nodes.get('gpt-changes-list').innerHTML, /gpt-tag-user/);
  assert.match(app.nodes.get('gpt-changes-list').innerHTML, /&lt;img src=x&gt;/);
  app.nodes.get('gpt-changes-query').value = 'reader';
  app.nodes.get('gpt-changes-source').value = 'user';
  app.nodes.get('gpt-changes-filter').listeners.submit[0]({ preventDefault() {} });
  await flush();
  assert.ok(app.calls.some(([url]) => url.endsWith('?q=reader&source=user&offset=0')));
  await app.click('gpt-changes-list', '[data-review-change]', { reviewChange: 'event-1' });
  assert.ok(app.calls.some(([url, options]) => url.endsWith('/changes/event-1/review') && options.method === 'POST'));
  assert.match(app.nodes.get('gpt-changes-list').innerHTML, /恢复2FA 密钥/);
  await app.click('gpt-changes-list', '[data-restore-change]', { restoreChange: 'event-1', restoreField: 'secret', restoreLabel: '2FA 密钥' });
  const [, restore] = app.calls.find(([url]) => url.endsWith('/changes/event-1/restore'));
  assert.equal(restore.method, 'POST');
  assert.deepEqual(JSON.parse(restore.body), { field: 'secret' });
});

test('standalone mailbox resources use full-history scope independently of GPT details', async () => {
  const app = page('static/index.html', true, 'reader'); await flush();
  assert.match(app.nodes.get('mailbox-resource-rows').innerHTML, /321321|SiliconFlow/);
  assert.match(app.nodes.get('mailbox-resource-rows').innerHTML, /claude\.ai\/login#mail-only/);
  await app.click('mailbox-resource-rows', '[data-mailbox-history]', { mailboxHistory: 'account-1' });
  assert.ok(app.calls.some(([url]) => url === '/api/history/account-1?scope=mailbox&preview=1&user_id=reader'));
  assert.match(app.nodes.get('mailbox-resource-rows').innerHTML, /654321/);
  assert.doesNotMatch(app.nodes.get('rows').innerHTML, /654321/);
  app.context.fetch = async url => ({ ok: true, json: async () => ({items: []}) });
  await [...app.timers.values()].at(-1)(); await flush();
  assert.doesNotMatch(app.nodes.get('mailbox-resource-rows').innerHTML, /654321|claude\.ai/);
  assert.match(app.nodes.get('mailbox-resource-rows').innerHTML, /暂无可用邮箱/);
});

test('console changes mailbox listing without toggling its listener', async () => {
  const app = page('static/admin.html'); await flush();
  await app.click('mailbox-rows', '[data-mail-listing]', { name: 'account-1', listed: 'false' });
  const call = app.calls.find(([url]) => url === '/api/admin/mail/accounts/account-1/listing');
  assert.equal(call[1].method, 'PUT');
  assert.equal(JSON.parse(call[1].body).listed, false);
  assert.ok(!app.calls.some(([url, options]) => /account-1\/(enable|disable)$/.test(url) && options?.method === 'POST'));
});

test('mailbox notes render as text and can be edited independently of listing and listeners', async () => {
  for (const file of ['static/index.html', 'static/admin.html']) {
    const app = page(file); await flush();
    const html = app.nodes.get(file === 'static/index.html' ? 'mailbox-resource-rows' : 'mailbox-rows').innerHTML;
    assert.match(html, /硅基流动登录码 &lt;img src=x onerror=alert\(1\)&gt;/);
    assert.doesNotMatch(html, /<img src=x/);
  }
  const app = page('static/admin.html'); await flush();
  await app.click('mailbox-rows', '[data-mail-note]', { name: 'account-1' });
  assert.equal(app.nodes.get('mail-note-input').value, mailboxFixture()[0].note);
  app.nodes.get('mail-note-input').value = '硅基流动、Kimi 验证码';
  await app.nodes.get('mail-note-form').listeners.submit[0]({ preventDefault() {} });
  const request = app.calls.find(([url]) => url === '/api/admin/mail/accounts/account-1/note');
  assert.equal(request[1].method, 'PUT');
  assert.deepEqual(JSON.parse(request[1].body), { note: '硅基流动、Kimi 验证码' });
  assert.ok(!app.calls.some(([url, options]) => /account-1\/(listing|enable|disable)$/.test(url) && options?.method !== 'GET'));
});

test('subscription dates label expiry and use old or undated authorization before the manual fallback', async () => {
  for (const file of ['static/index.html', 'static/admin.html']) {
    const app = page(file); await flush();
    const expiry = new Date(2026, 1, 28, 12).getTime() / 1000;
    const manual = {subscription_started_on:'2025-01-31', subscription_ends_on:'2025-02-28'};
    const render = (codex, day = 23, hour = 12) => vm.runInContext(`renderAccountSubscription(${JSON.stringify({...manual, codex})},escapeHtml,new Date(2026,1,${day},${hour}))`, app.context);
    const checked = new Date(2025, 1, 22, 12).getTime() / 1000;
    const automatic = [{subscription:{source:'id_token',available:true,expires_at:expiry,checked_at:checked},access_token_expires_at:expiry+999999}];
    let html = render(automatic);
    assert.match(html, /订阅到期：26\.02\.28 \(5 Day left\)/);
    assert.match(html, /授权文件 ID Token（到期日）/);
    assert.match(html, /授权上次核对：/);
    assert.match(html, /subscription-due/);
    assert.doesNotMatch(html, /25\.01\.31|已到期/);
    assert.doesNotMatch(render(automatic, 22), /subscription-due/);
    assert.match(render(automatic, 28, 8), /1 Day left/);
    assert.match(render(automatic, 28, 12), /已到期/);
    html = render([{subscription:{}},...automatic]);
    assert.match(html, /授权文件 ID Token/);
    assert.match(html, /26\.02\.28/);
    for (const reason of ['missing_dates','invalid_dates','account_mismatch']) {
      html = render([{subscription:{source:'id_token',available:false,checked_at:checked,expires_at:null,unavailable_reason:reason}}]);
      assert.match(html, /订阅开通：25\.01\.31/);
      assert.match(html, /已回退手填/);
      assert.doesNotMatch(html, /授权文件 ID Token/);
    }
    html = render([{...automatic[0],subscription_error:'retired 403',reauth_required:true}]);
    assert.match(html, /授权文件 ID Token/);
    html = render([{subscription:{...automatic[0].subscription,checked_at:null}}]);
    assert.match(html, /授权文件 ID Token/);
    assert.match(html, /授权上次核对：无记录/);
    assert.doesNotMatch(html, /手填开通日/);
    html = vm.runInContext(`renderAccountSubscription(${JSON.stringify({codex:[{subscription:{source:'id_token',available:false,checked_at:checked,unavailable_reason:'missing_dates'}}]})},escapeHtml,new Date(2026,1,23,12))`, app.context);
    assert.match(html, /未设置/);
    assert.match(html, /授权日期缺失/);
    assert.match(html, /请管理员补充/);
    html = render([{access_token_expires_at:expiry,usage:{resets_at:expiry},subscription:{expires_at:0,renews_at:'invalid'}}]);
    assert.match(html, /25\.01\.31 \(已到期\)/);
    assert.match(html, /手填开通日/);
  }
});

test('Coding Plan configuration follows the provider and keeps copy beside the masked key', async () => {
  for (const file of ['static/index.html', 'static/admin.html']) {
    const app = page(file); await flush();
    for (const [provider, doc, urlCount] of [['xiaomi', '小米 MiMo', 2], ['zhipu', '智谱 GLM', 3], ['kimi', 'Kimi Code', 2]]) {
      const html = vm.runInContext(`codingPlanRow(${JSON.stringify({id:'plan-1',name:'Demo',provider,api_key_masked:'tp-••••key'})})`, app.context);
      const cells = [...html.matchAll(/<td>([\s\S]*?)<\/td>/g)].map(match => match[1]);
      assert.match(cells[0], new RegExp(doc));
      assert.match(cells[0], /target="_blank" rel="noopener noreferrer"/);
      assert.match(cells[1], /tp-••••key<\/span><button/);
      assert.equal((cells[2].match(/data-base-url=/g) || []).length, urlCount);
    }
    const before = app.calls.length;
    await app.click('coding-plan-rows', '[data-base-url]', {baseUrl:'https://api.kimi.com/coding/v1'});
    assert.equal(app.calls.length, before + 1);
    assert.deepEqual(app.calls.at(-1), ['clipboard', 'https://api.kimi.com/coding/v1']);
  }
});

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
  assert.equal(app.timers.size, 3);
});

test('website account console exposes the admin-only batch user form', () => {
  const root = process.env.MAILAPI_UI_ROOT || 'static';
  const html = fs.readFileSync(`${root}/access.html`, 'utf8');
  const script = fs.readFileSync(`${root}/access.js`, 'utf8');
  assert.match(html, /id="batch-users"[^>]*>批量/);
  assert.match(html, /id="batch-user-names|id="batch-usernames/);
  assert.match(html, /统一初始密码/);
  assert.match(script, /\/api\/admin\/users\/batch/);
  assert.match(script, /currentUser\.role !== 'admin'/);
});

test('Codex-only account can copy both credentials and omits mailbox details', async () => {
  const app = page('static/index.html'); await flush();
  await app.click('rows', '[data-expand-key]', { expandKey: 'email:only@example.com' });
  assert.doesNotMatch(app.nodes.get('rows').innerHTML, /无邮箱验证/);
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

test('username-only and password-only GPT accounts omit unconfigured login capabilities', async () => {
  const app = page('static/index.html'); await flush();
  const item = {
    key: 'account:standalone', login: 'team-login', email: 'team-login',
    has_gpt_password: false, has_totp_secret: false, codex: [], mail: [],
  };
  app.context.fetch = async () => ({ ok: true, json: async () => ({ items: [item] }) });
  await vm.runInContext('refreshAccounts()', app.context);
  assert.match(app.nodes.get('rows').innerHTML, /team-login/);
  assert.match(app.nodes.get('rows').innerHTML, /仅账号名/);
  assert.doesNotMatch(app.nodes.get('rows').innerHTML, /data-expand-key|tag-email|身份验证器/);
  item.has_gpt_password = true;
  item.gpt_password_account = item.key;
  await vm.runInContext('refreshAccounts()', app.context);
  await app.click('rows', '[data-expand-key]', { expandKey: item.key });
  assert.match(app.nodes.get('rows').innerHTML, /GPT密码/);
  assert.doesNotMatch(app.nodes.get('rows').innerHTML, /身份验证器|email-detail/);
});

test('2FA countdown tracks copied code expiry across refreshes and prompts recopy', async () => {
  const app = page('static/index.html'); await flush();
  let now = 65000;
  app.context.Date = class extends Date { static now() { return now; } };
  app.context.performance.now = () => now;
  vm.runInContext('serverClockOffset = 0', app.context);
  const name = 'email:only@example.com';
  const validity = { dataset: { totpValidity: name } };
  app.nodes.get('rows').querySelectorAll = () => [validity];
  await app.click('rows', '[data-expand-key]', { expandKey: 'email:only@example.com' });
  assert.match(app.nodes.get('rows').innerHTML, /data-copy-type="totp"[^>]*>复制<\/button><span data-totp-validity=/);
  assert.equal(validity.textContent, '当前码剩余 25 秒');
  app.context.fetch = async () => ({ ok: true, json: async () => ({ code: '081804', expires_at: 90 }) });
  await app.click('rows', '[data-copy-type]', { copyType: 'totp', name });
  assert.equal(validity.textContent, '已复制码剩余 25 秒');
  now = 86000;
  for (const tick of app.timers.values()) tick();
  assert.equal(validity.textContent, '已复制码剩余 4 秒');
  assert.equal(validity.className, 'status-warn');
  now = 90000;
  vm.runInContext('render()', app.context);
  assert.equal(validity.textContent, '已复制码已过期，请重新复制');
  app.context.fetch = async () => ({ ok: true, json: async () => ({ code: '123456', expires_at: 120 }) });
  await app.click('rows', '[data-copy-type]', { copyType: 'totp', name });
  assert.equal(validity.textContent, '已复制码剩余 30 秒');
});

test('refresh failure preserves current account rows and displays an error', async () => {
  const app = page('static/index.html'); await flush();
  const html = app.nodes.get('rows').innerHTML;
  app.context.fetch = async () => ({ ok: false });
  await vm.runInContext('refreshAccounts()', app.context);
  assert.equal(app.nodes.get('rows').innerHTML, html);
  assert.match(app.nodes.get('load-error').textContent, /刷新失败/);
});

test('user preview uses the ordinary account API view for all interactions', async () => {
  const app = page('static/index.html', true); await flush();
  assert.ok(app.calls.some(([url]) => url === '/api/accounts?preview=1'));
  await app.click('rows', '[data-expand-key]', { expandKey: 'email:user@example.com' });
  await app.click('rows', '[data-history-name]', { historyName: 'account-1' });
  for (const type of ['password', 'totp']) await app.click('rows', '[data-copy-type]', { copyType: type, name: 'email:user@example.com' });
  assert.ok(app.calls.some(([url]) => url === '/api/history/account-1?preview=1'));
  assert.ok(app.calls.some(([url]) => url === '/api/accounts/email%3Auser%40example.com/gpt-password?preview=1'));
  assert.ok(app.calls.some(([url]) => url === '/api/accounts/email%3Auser%40example.com/totp-code?preview=1'));
});

test('test-version preview forwards its selected website user to every account interaction', { skip: !process.env.MAILAPI_UI_ROOT }, async () => {
  const app = page('static/index.html', true, 'selected-user'); await flush();
  await app.click('rows', '[data-expand-key]', { expandKey: 'email:user@example.com' });
  await app.click('rows', '[data-history-name]', { historyName: 'account-1' });
  await app.click('rows', '[data-copy-type]', { copyType: 'password', name: 'email:user@example.com' });
  for (const [url] of app.calls.filter(([url]) => url.startsWith('/api/'))) assert.match(url, /preview=1&user_id=selected-user/);
});

test('mail links render as truncated external anchors and can be copied', async () => {
  const app = page('static/index.html'); await flush();
  const value = 'https://claude.ai/login?token=long-token-value';
  const html = vm.runInContext(`mailResultHtml('link', ${JSON.stringify(value)})`, app.context);
  assert.match(html, /class="mail-link"/);
  assert.match(html, /href="https:\/\/claude\.ai\/login\?token=long-token-value"/);
  assert.match(html, /target="_blank"/);
  assert.match(html, /data-copy-type="mail"/);
  await app.click('rows', '[data-copy-type]', { copyType: 'mail', value });
  assert.ok(app.calls.some(([action, copied]) => action === 'clipboard' && copied === value));
});

test('mail results show source tags beside the subject and keep the result label separate', async () => {
  const app = page('static/index.html'); await flush();
  assert.match(vm.runInContext("mailSourceTag('OpenAI 验证码', '')", app.context), /mail-source-openai\">OpenAI/);
  assert.match(vm.runInContext("mailSourceTag('', 'Your temporary ChatGPT login code')", app.context), /mail-source-openai\">OpenAI/);
  assert.match(vm.runInContext("mailSourceTag('', 'Your secure link to Claude')", app.context), /mail-source-anthropic\">Anthropic/);
  const html = vm.runInContext(`detailRow(${JSON.stringify({
    credentials: {},
    mails: [{ name: 'account-1', result_type: 'link', result_value: 'https://claude.ai/magic-link#token', rule_name: 'Anthropic / Claude 登录', subject: 'Claude', updated_at: '' }],
  })})`, app.context);
  assert.match(html, /class="email-result-label">最新接收：/);
  assert.match(html, /class="mail-source-tag mail-source-anthropic">Anthropic/);
  assert.match(html, /class="mail-subject-line"/);
});

test('2FA clock ignores slow samples and wall-clock jumps and catches up after paused ticks', async () => {
  const app = page('static/index.html'); await flush();
  let elapsed = 1000;
  app.context.performance.now = () => elapsed;
  vm.runInContext('syncServerClock({ server_time: 65.05 }, 900)', app.context);
  assert.equal(vm.runInContext("totpValidity('test').text", app.context), '当前码剩余 25 秒');
  elapsed = 2000;
  vm.runInContext('syncServerClock({ server_time: 60 }, 0)', app.context);
  app.context.Date = class extends Date { static now() { return 9999999999999; } };
  assert.equal(vm.runInContext("totpValidity('test').text", app.context), '当前码剩余 24 秒');
  elapsed = 26000;
  vm.runInContext("copiedTotpExpiry.set('test', 90)", app.context);
  assert.equal(vm.runInContext("totpValidity('test').text", app.context), '已复制码已过期，请重新复制');
});

test('console separates GPT and mailbox rows and routes credential changes', async () => {
  const app = page('static/admin.html'); await flush();
  const html = app.nodes.get('account-rows').innerHTML;
  assert.equal((html.match(/<tr>/g) || []).length, 2);
  assert.match(html, /GPT密码/); assert.match(html, /监听邮箱/); assert.match(html, /移除 Codex/); assert.match(html, /删除 GPT 账号/);
  assert.doesNotMatch(html, /data-mail-action/);
  assert.match(app.nodes.get('mailbox-rows').innerHTML, /移除邮箱/);
  await app.click('account-rows', '[data-password-action]', { passwordAction: 'edit', name: 'email:only@example.com', email: 'only@example.com' });
  assert.equal(app.nodes.get('password-dialog').open, true);
  app.nodes.get('password-input').value = 'new-password';
  await app.nodes.get('password-form').listeners.submit[0]({ preventDefault() {} });
  assert.ok(app.calls.some(([url, options]) => url === '/api/admin/accounts/email%3Aonly%40example.com/gpt-password' && options.method === 'PUT'));
  await app.click('account-rows', '[data-delete-account]', { deleteAccount: 'email:user@example.com', email: 'user@example.com' });
  assert.ok(app.calls.some(([url, options]) => url === '/api/admin/accounts/email%3Auser%40example.com' && options.method === 'DELETE'));
});

test('console can unlist and relist an account and preserves the row on failure', async () => {
  const app = page('static/admin.html'); await flush();
  const endpoint = '/api/admin/accounts/email%3Auser%40example.com/listing';
  let listed = true;
  app.context.fetch = async (url, options) => {
    app.calls.push([url, options]);
    if (url === endpoint) listed = JSON.parse(options.body).listed;
    const items = fixture(); items[0].listed = listed;
    return { ok: true, json: async () => ({ items }) };
  };
  assert.match(app.nodes.get('account-rows').innerHTML, /data-listed="false"[^>]*>下架账号/);
  await app.click('account-rows', '[data-listing-account]', { listingAccount: 'email:user@example.com', listed: 'false' });
  assert.ok(app.calls.some(([url, options]) => url === endpoint && options.method === 'PUT' && options.body === '{"listed":false}'));
  assert.match(app.nodes.get('account-rows').innerHTML, /已下架/);
  assert.match(app.nodes.get('account-rows').innerHTML, /data-listed="true"[^>]*>上架账号/);
  await app.click('account-rows', '[data-listing-account]', { listingAccount: 'email:user@example.com', listed: 'true' });
  assert.match(app.nodes.get('account-rows').innerHTML, /已上架/);
  const html = app.nodes.get('account-rows').innerHTML;
  app.context.fetch = async () => ({ ok: false, json: async () => ({ detail: '保存失败' }) });
  const button = await app.click('account-rows', '[data-listing-account]', { listingAccount: 'email:user@example.com', listed: 'false' });
  assert.equal(button.disabled, false);
  assert.equal(app.nodes.get('gpt-message').textContent, '保存失败');
  assert.equal(app.nodes.get('account-rows').innerHTML, html);
});


test('Codex login prepares a direct external URL without opening an intermediate popup', async () => {
  const app = page('static/admin.html'); await flush();
  const authUrl = 'https://auth.openai.com/oauth/authorize?state=test';
  app.context.fetch = async (url, options) => { app.calls.push([url, options]); return { ok: true, json: async () => ({ auth_url: authUrl }) }; };
  await app.click('account-rows', '[data-codex-auth]', { codexAuth: 'admin-reauth', id: 'codex-1' });
  assert.equal(app.nodes.get('codex-auth-dialog').open, true);
  assert.equal(app.nodes.get('codex-auth-open').href, authUrl);
  assert.equal(app.nodes.get('codex-auth-open').hidden, false);
  assert.ok(app.calls.some(([url, options]) => url.includes('format=json') && url.includes('account_id=codex-1') && options.method === 'POST'));
  await app.nodes.get('codex-auth-copy').listeners.click[0]();
  assert.ok(app.calls.some(([action, value]) => action === 'clipboard' && value === authUrl));
});

test('Codex login failures remain visible in the console dialog', async () => {
  const app = page('static/admin.html'); await flush();
  app.context.fetch = async () => ({ ok: false, status: 500, json: async () => ({ detail: '回调端口无法监听' }) });
  await app.click('account-rows', '[data-codex-auth]', { codexAuth: 'admin-import' });
  assert.equal(app.nodes.get('codex-auth-status').textContent, '回调端口无法监听');
  assert.equal(app.nodes.get('codex-auth-open').hidden, true);
  assert.equal(app.nodes.get('codex-auth-copy').disabled, true);
});

test('homepage and console render relay names as links and retain legacy rows without a URL', async () => {
  const item = { id: 'relay-1', name: '中转站 <团队>', url: 'https://relay.example/panel?a=1&b=2', account: 'user', password_masked: '••••••' };
  for (const file of ['static/index.html', 'static/admin.html']) {
    const app = page(file); await flush();
    const html = vm.runInContext(`relayAccountRow(${JSON.stringify(item)})`, app.context);
    assert.match(html, /href="https:\/\/relay\.example\/panel\?a=1&amp;b=2"/);
    assert.match(html, /target="_blank" rel="noopener noreferrer">中转站 &lt;团队&gt;<\/a>/);
    const legacy = vm.runInContext(`relayAccountRow(${JSON.stringify({ ...item, url: '' })})`, app.context);
    assert.doesNotMatch(legacy, /href=/);
    assert.match(legacy, /中转站 &lt;团队&gt;/);
  }
});

test('console loads the relay website for edits and submits it for new accounts', async () => {
  const app = page('static/admin.html'); await flush();
  vm.runInContext(`openRelayAccountDialog({ id: 'relay-1', name: '旧中转站', url: 'https://old.example', account: 'user' })`, app.context);
  assert.equal(app.nodes.get('relay-account-url').value, 'https://old.example');
  vm.runInContext('openRelayAccountDialog()', app.context);
  assert.equal(app.nodes.get('relay-account-url').value, '');
  app.nodes.get('relay-account-name').value = '新中转站';
  app.nodes.get('relay-account-url').value = ' https://new.example/panel ';
  app.nodes.get('relay-account-login').value = 'new-user';
  app.nodes.get('relay-account-password').value = 'new-password';
  await app.nodes.get('relay-account-form').listeners.submit[0]({ preventDefault() {} });
  const request = app.calls.find(([url, options]) => url === '/api/admin/relay-accounts' && options.method === 'POST');
  assert.ok(request);
  assert.equal(JSON.parse(request[1].body).url, 'https://new.example/panel');
});

test('relay passwords are optional and passwordless rows omit the copy button', async () => {
  const app = page('static/admin.html'); await flush();
  vm.runInContext('filters = [{id: "siliconflow", name: "硅基流动验证码"}]; openRelayAccountDialog()', app.context);
  assert.equal(app.nodes.get('relay-account-password').required, false);
  app.nodes.get('relay-account-name').value = '硅基流动';
  app.nodes.get('relay-account-url').value = 'https://relay.example.com';
  app.nodes.get('relay-account-login').value = 'user@example.com';
  const mailbox = app.nodes.get('relay-account-mailbox');
  assert.match(mailbox.innerHTML, /value="mailbox:two">mail-only@example.com/);
  mailbox.value = 'mailbox:two';
  const rule = app.nodes.get('relay-account-mail-rule');
  assert.match(rule.innerHTML, /value="siliconflow">硅基流动验证码/);
  rule.value = 'siliconflow';
  await app.nodes.get('relay-account-form').listeners.submit[0]({ preventDefault() {} });
  const request = app.calls.find(([url, options]) => url === '/api/admin/relay-accounts' && options.method === 'POST');
  assert.equal(Object.hasOwn(JSON.parse(request[1].body), 'password'), false);
  assert.equal(JSON.parse(request[1].body).mailbox_key, 'mailbox:two');
  assert.equal(JSON.parse(request[1].body).mail_rule_id, 'siliconflow');
  const home = page('static/index.html'); await flush();
  const item = { id: 'relay-1', name: '硅基流动', account: 'user@example.com', has_password: false, password_masked: '未设置' };
  const html = vm.runInContext(`relayAccountRow(${JSON.stringify(item)})`, home.context);
  assert.match(html, /未设置登录方式/);
  assert.doesNotMatch(html, /data-resource-copy="relay-password"/);
  const linked = vm.runInContext(`relayAccountRow(${JSON.stringify({ ...item, has_mailbox: true })})`, home.context);
  assert.match(linked, /data-relay-mail="relay-1"[^>]*>验证码/);
  assert.doesNotMatch(linked, /未设置登录方式|data-resource-copy/);
});

test('relay verification opens a preview-scoped mailbox and clears results after access is lost', async () => {
  const app = page('static/index.html', true, 'reader'); await flush();
  const path = '/api/relay-accounts/relay-1/mailbox?preview=1&user_id=reader';
  let allowed = true;
  app.context.fetch = async url => {
    assert.equal(url, path);
    return allowed
      ? { ok: true, json: async () => ({ item: mailboxFixture()[0], history: [{ code: '654321', subject: 'Historical code' }] }) }
      : { ok: false, status: 404 };
  };
  await app.click('relay-account-rows', '[data-relay-mail]', { relayMail: 'relay-1' });
  const dialog = app.nodes.get('relay-mail-dialog');
  const content = app.nodes.get('relay-mail-content');
  assert.equal(dialog.open, true);
  assert.match(content.innerHTML, /321321/);
  assert.match(content.innerHTML, /654321/);
  allowed = false;
  await [...app.timers.values()].at(-1)();
  assert.match(content.textContent, /分组权限已变更/);
  const timerCount = app.timers.size;
  dialog.close();
  dialog.listeners.close[0]();
  assert.equal(app.timers.size, timerCount - 1);
  assert.equal(content.textContent, '');
});

test('homepage and console render each Coding Plan window with the Codex quota meter', async () => {
  const item = { id: 'plan-1', name: '编码套餐', provider: 'kimi', api_key_masked: '••••••', quota_query_enabled: true,
    quota: { updated_at: '2026-11-06T00:00:00Z', windows: [
      { label: '5 小时', remaining_percent: 0, reset_at: '2026-11-06T01:00:00Z' },
      { label: '每周', remaining_percent: 72.5 },
    ] } };
  for (const file of ['static/index.html', 'static/admin.html']) {
    const app = page(file); await flush();
    const codex = vm.runInContext('quotaCell({ used_percent: 25 })', app.context);
    assert.match(codex, /class="quota-track" role="meter"/);
    assert.match(codex, /class="quota-fill" style="width:75%"/);
    const html = vm.runInContext(`codingPlanRow(${JSON.stringify(item)})`, app.context);
    assert.match(html, /Kimi Code/);
    assert.match(html, /class="quota-cell quota-empty"/);
    assert.match(html, /class="quota-value">0%/);
    assert.match(html, /role="meter" aria-label="5 小时剩余比例"[^>]*aria-valuenow="0"/);
    assert.match(html, /class="quota-fill" style="width:0%"/);
    assert.match(html, /role="meter" aria-label="每周剩余比例"[^>]*aria-valuenow="72.5"/);
    assert.match(html, /class="quota-fill" style="width:72.5%"/);
    assert.match(html, /重置/);
    assert.match(html, /更新于/);
    if (file.endsWith('index.html')) assert.match(html, /data-resource-copy="coding-plan"/);
    assert.ok(!app.calls.some(([url]) => url.includes('/quota')));
    const xiaomi = { ...item, provider: 'xiaomi', quota: { windows: [
      { label: '周期额度', remaining_percent: 85, period_end_at: '2027-09-21T23:59:59Z' },
    ] } };
    const xiaomiHtml = vm.runInContext(`codingPlanRow(${JSON.stringify(xiaomi)})`, app.context);
    assert.match(xiaomiHtml, /小米 MiMo Token Plan/);
    assert.match(xiaomiHtml, /role="meter" aria-label="周期额度剩余比例"[^>]*aria-valuenow="85"/);
    assert.match(xiaomiHtml, /周期结束/);
  }
});

test('visible homepage and console automatically query due plans while preserving preview scope', async () => {
  for (const [file, refresh, suffix] of [
    ['static/index.html', 'refreshResources()', '?preview=1&user_id=selected-user'],
    ['static/admin.html', 'loadCodingPlans()', ''],
  ]) {
    const app = page(file, Boolean(suffix), 'selected-user'); await flush();
    const item = { id: 'plan-1', provider: 'xiaomi', quota_query_enabled: true, quota_refresh_due: true };
    const queries = [];
    app.context.fetch = async (url, options) => {
      if (url.includes('/quota')) queries.push([url, options.method]);
      return { ok: true, json: async () => ({ items: url === `/api/coding-plans${suffix}` ? [item] : [] }) };
    };
    app.context.document.hidden = true;
    await vm.runInContext(refresh, app.context);
    assert.equal(queries.length, 0);
    app.context.document.hidden = false;
    await vm.runInContext(refresh, app.context);
    assert.deepEqual(queries, [[`/api/coding-plans/plan-1/quota${suffix}`, 'POST']]);
    item.quota_refresh_due = false;
    await vm.runInContext(refresh, app.context);
    assert.equal(queries.length, 1);
  }
});

test('admin can enable Xiaomi quota for an existing plan and retain its key', async () => {
  const admin = page('static/admin.html'); await flush();
  vm.runInContext(`openCodingPlanDialog({ id: 'plan-1', name: '旧套餐' })`, admin.context);
  admin.nodes.get('coding-plan-provider').value = 'xiaomi';
  await admin.nodes.get('coding-plan-form').listeners.submit[0]({ preventDefault() {} });
  const request = admin.calls.find(([url, options]) => url === '/api/admin/coding-plans/plan-1' && options.method === 'PUT');
  assert.equal(JSON.parse(request[1].body).provider, 'xiaomi');
  assert.ok(!Object.hasOwn(JSON.parse(request[1].body), 'api_key'));
});
