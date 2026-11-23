const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../../mailapi_shared/static');
const tick = () => new Promise(resolve => setImmediate(resolve));

function setup(t, {required = false, handler, admin = false} = {}) {
  const host = `<section id="login-challenge" data-sitekey="public-key" data-required="${Number(required)}" ${required ? '' : 'hidden'}>
    <div id="login-challenge-widget"></div><p id="login-challenge-status" role="status"></p>
    <button id="login-challenge-retry" type="button" hidden>重新加载验证</button></section>`;
  const html = fs.readFileSync(path.join(root, 'login.html'), 'utf8').replace('<!-- LOGIN_CHALLENGE -->', host);
  const dom = new JSDOM(html, {url: 'https://testserver/login' + (admin ? '?mode=admin' : ''), runScripts: 'outside-only'});
  t.after(() => dom.window.close());
  const w = dom.window;
  w.AbortController = AbortController;
  for (const script of w.document.querySelectorAll('script:not([src])')) w.eval(script.textContent);
  const form = w.document.getElementById('login-form');
  form.elements.username.value = 'alice';
  form.elements.password.value = 'private-password';
  if (admin) form.elements.totp_code.value = '123456';
  const calls = [];
  w.fetch = async (url, options) => {
    calls.push([url, options]);
    return handler ? handler(url, options) : {ok: false, status: 401, json: async () => ({detail: '用户名、密码或动态验证码错误'})};
  };
  w.eval(fs.readFileSync(path.join(root, 'login-protection.js'), 'utf8'));
  const send = () => form.dispatchEvent(new w.Event('submit', {bubbles: true, cancelable: true}));
  function provider() {
    const options = [];
    let resets = 0;
    w.turnstile = {render: (selector, value) => {options.push(value); return 0;}, reset: () => {resets++;}, remove: () => {}};
    w.mailapiTurnstileReady();
    return {options, resets: () => resets};
  }
  return {w, form, calls, send, provider, challenge: w.document.getElementById('login-challenge')};
}

test('normal login loads no third-party widget, preserves admin OTP and prevents duplicate submits', async t => {
  let finish;
  const {w, form, calls, send, challenge} = setup(t, {admin: true, handler: () => new Promise(resolve => {finish = resolve;})});
  assert.equal(challenge.hidden, true);
  assert.equal(w.document.querySelector('script[src^="https:"]'), null);
  assert.equal(form.action, 'https://testserver/login?mode=admin');
  send(); send();
  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(calls[0][1].body), {
    username: 'alice', password: 'private-password', totp_code: '123456', turnstile_token: null,
  });
  assert.equal(calls[0][1].credentials, 'same-origin');
  assert.equal(form.querySelector('[type=submit]').disabled, true);
  finish({ok: false, json: async () => ({detail: '用户名、密码或动态验证码错误'})});
  await tick();
  assert.equal(form.querySelector('[type=submit]').disabled, false);
  assert.match(form.querySelector('.error').textContent, /用户名/);
  assert.equal(w.document.querySelector('script[src^="https:"]'), null);
});

test('challenge appears only on server demand and never submits credentials before it is solved', async t => {
  let count = 0;
  const state = setup(t, {handler: async () => ({ok: false, json: async () => {
    count++;
    return {detail: '请先完成安全验证', challenge: {site_key: 'public-key'}};
  }})});
  const {w, send, calls, form, challenge} = state;
  send(); await tick();
  assert.equal(challenge.hidden, false);
  const script = w.document.querySelector('script[src^="https:"]');
  assert.match(script.src, /^https:\/\/challenges.cloudflare.com\/turnstile\/v0\/api.js\?/);
  const p = state.provider(); await tick();
  assert.equal(p.options[0].action, 'login');
  assert.equal(p.options[0].size, 'flexible');
  send(); await tick();
  assert.equal(calls.length, 1);
  assert.equal(form.querySelector('.error').textContent, '请先完成安全验证');
  p.options[0].callback('single-use-token');
  send(); await tick();
  assert.equal(count, 2);
  assert.equal(JSON.parse(calls[1][1].body).turnstile_token, 'single-use-token');
  assert.equal(p.resets(), 1);
  send(); await tick();
  assert.equal(calls.length, 2);
  assert.equal(w.localStorage.length, 0);
  assert.equal(w.sessionStorage.length, 0);
});

test('an unavailable provider remains blocked and offers an accessible retry instead of password fallback', async t => {
  const {w, form, calls, send} = setup(t, {required: true});
  const script = w.document.querySelector('script[src^="https:"]');
  script.dispatchEvent(new w.Event('error')); await tick();
  const retry = w.document.getElementById('login-challenge-retry');
  assert.equal(retry.hidden, false);
  assert.match(w.document.getElementById('login-challenge-status').textContent, /暂时不可用/);
  send(); await tick();
  assert.equal(calls.length, 0);
  assert.equal(form.querySelector('[type=submit]').disabled, false);
  retry.click(); await tick();
  assert.equal(w.document.querySelectorAll('script[src^="https:"]').length, 1);
  assert.equal(retry.hidden, true);
});

test('expired verification cannot be reused, and server unavailability clears the used token', async t => {
  const state = setup(t, {required: true, handler: async () => ({ok: false, status: 503, json: async () => ({
    detail: '安全验证暂时不可用，请稍后重试', code: 'verification_unavailable', challenge: {site_key: 'public-key'},
  })})});
  const {w, send, calls, form} = state;
  const p = state.provider(); await tick();
  p.options[0].callback('expired-token');
  p.options[0]['expired-callback']();
  send(); await tick(); assert.equal(calls.length, 0);
  p.options[0].callback('new-token');
  send(); await tick(); assert.equal(calls.length, 1);
  assert.match(form.querySelector('.error').textContent, /暂时不可用/);
  send(); await tick(); assert.equal(calls.length, 1);
  assert.equal(form.querySelector('[type=submit]').disabled, false);
  assert.equal(w.document.getElementById('login-challenge').hidden, false);
});

test('provider errors and network failures leave a readable message and never auto-resubmit', async t => {
  const state = setup(t, {required: true, handler: async () => {throw new Error('private network error');}});
  const p = state.provider(); await tick();
  assert.equal(p.options[0]['error-callback'](), true);
  assert.equal(state.w.document.getElementById('login-challenge-retry').hidden, false);
  p.options[0].callback('token');
  state.send(); await tick();
  assert.equal(state.calls.length, 1);
  assert.equal(state.form.querySelector('.error').textContent, '网络连接失败，请检查网络后重试');
  assert.equal(state.form.querySelector('[type=submit]').disabled, false);
  assert.equal(state.w.document.body.textContent.includes('private network error'), false);
});

test('login error content is text rather than HTML and supports the existing language control', async t => {
  const {w, form, send} = setup(t, {handler: async () => ({ok: false, json: async () => ({detail: '<img src=x onerror=alert(1)>'})})});
  w.SiteLanguage = {t: value => 'English: ' + value};
  send(); await tick();
  assert.equal(form.querySelector('.error img'), null);
  assert.match(form.querySelector('.error').textContent, /^English: <img/);
});

test('switching usernames clears the old challenge and ignores its late token', async t => {
  const state = setup(t, {required: true});
  const p = state.provider(); await tick();
  state.form.elements.username.value = 'another-user';
  state.form.elements.username.dispatchEvent(new state.w.Event('input'));
  assert.equal(state.challenge.hidden, true);
  p.options[0].callback('old-user-token');
  state.send(); await tick();
  assert.equal(state.calls.length, 1);
  assert.equal(JSON.parse(state.calls[0][1].body).turnstile_token, null);
  assert.equal(JSON.parse(state.calls[0][1].body).username, 'another-user');
});
