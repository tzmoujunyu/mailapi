const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '../../mailapi_shared/static');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');

test('every test page switches languages and restores its original interface text', async t => {
  for (const name of ['login.html', 'index.html', 'access.html', 'admin.html', 'test-features.html']) {
    const dom = new JSDOM(read(name), {url: 'http://localhost/login', runScripts: 'outside-only', pretendToBeVisual: true});
    t.after(() => dom.window.close());
    const w = dom.window;
    w.eval(read('test-i18n-en.js'));
    w.eval(read('test-i18n.js'));
    const heading = w.document.querySelector('h1');
    const original = heading.textContent;
    await w.SiteLanguage.set('en');
    assert.equal(w.document.documentElement.lang, 'en');
    assert.notEqual(heading.textContent, original, name);
    await w.SiteLanguage.set('zh-CN');
    assert.equal(heading.textContent, original, name);
  }
});

test('translation handles dynamic controls while preserving resource data, input values and rich text', async t => {
  const dom = new JSDOM('<html data-theme-account="signed-in"><body><h1>主页</h1><input value="密码"><div class="resource-name">主页</div><div contenteditable>保存</div><table><tbody><tr><td>用户名</td><td><button>保存</button></td></tr></tbody></table><section></section></body></html>', {url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true});
  t.after(() => dom.window.close());
  const w = dom.window, calls = [];
  w.fetch = async (url, options) => { calls.push([url, JSON.parse(options.body)]); return {ok: true}; };
  w.eval(read('test-i18n-en.js'));
  w.eval(read('test-i18n.js'));
  await w.SiteLanguage.set('en');
  assert.deepEqual(calls[0], ['/api/test-ui/preferences', {language: 'en'}]);
  assert.equal(w.document.querySelector('button').textContent, 'Save');
  assert.equal(w.document.querySelector('td').textContent, '用户名');
  assert.equal(w.document.querySelector('input').value, '密码');
  assert.equal(w.document.querySelector('.resource-name').textContent, '主页');
  assert.equal(w.document.querySelector('[contenteditable]').textContent, '保存');
  w.document.querySelector('section').innerHTML = '<button>新增公告</button><strong data-i18n-skip>新增公告</strong>';
  await new Promise(resolve => w.requestAnimationFrame(resolve));
  assert.equal(w.document.querySelector('section button').textContent, 'New announcement');
  assert.equal(w.document.querySelector('section strong').textContent, '新增公告');
  w.fetch = async () => ({ok: false});
  await assert.rejects(w.SiteLanguage.set('zh-CN'));
  assert.equal(w.document.documentElement.lang, 'en');
});

test('notification content is rendered as text and read receipts use the displayed revision', async t => {
  const dom = new JSDOM('<html lang="zh-CN"><body><div class="test-ui-header-actions"></div></body></html>', {url: 'http://localhost/', runScripts: 'outside-only'});
  t.after(() => dom.window.close());
  const w = dom.window, calls = [];
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  const item = {id: 'notice', title: '<img src=x onerror=alert(1)>', body: '<script>bad()</script>', updated_at: '2026-11-19', revision: 3, unread: true};
  w.fetch = async (url, options = {}) => { calls.push([url, options]); return {ok: true, json: async () => ({items: [item], can_manage: false})}; };
  w.eval(read('test-notifications.js'));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(w.document.querySelector('img'), null);
  assert.equal(w.document.querySelector('[data-create]').hidden, true);
  w.document.querySelector('.site-notice-title').click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(w.document.querySelector('[data-body]').textContent, item.body);
  assert.deepEqual(JSON.parse(calls.find(([url]) => url.endsWith('/read'))[1].body), {items: [{id: 'notice', revision: 3}]});
});

test('finance notices show safe historical results and open the authorized application after navigation',async t=>{
  const dom=new JSDOM('<html lang="zh-CN"><body><div class="test-ui-header-actions"></div></body></html>',{url:'http://localhost/',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,calls=[];let hasRead=false,navigated='',opened='';
  const item={id:12,application_id:'app-1',kind:'reviewed',version:1,current_version:2,current_status:'pending',created_at:'2026-11-22T00:00:00Z',unread:true,payload:{project:'<img src=x>',amount:'12.50',result:'rejected',note:'<script>bad()</script>'}};
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.TestNavigation={navigate:async url=>{navigated=url;}};w.document.addEventListener('finance:open-application',event=>{opened=event.detail.id;});
  w.fetch=async(url,options={})=>{calls.push([url,options]);if(url==='/api/finance/notifications/read')hasRead=true;return {ok:true,json:async()=>url.startsWith('/api/finance/notifications?')?{items:[{...item,unread:!hasRead}],total:1,unread_count:hasRead?0:1,max_id:12}:{items:[],can_manage:false}};};
  w.eval(read('test-notifications.js'));
  await new Promise(resolve=>setImmediate(resolve));
  w.document.querySelector('[data-notice-tab="finance"]').click();await new Promise(resolve=>setImmediate(resolve));
  assert.match(w.document.querySelector('.finance-notice-items').textContent,/申请当前状态: 待审核/);
  assert.equal(w.document.querySelector('img'),null);assert.equal(w.document.querySelector('script'),null);
  w.document.querySelector('.finance-notice-items button').click();await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));
  assert.equal(navigated,'/test-features#view-finance');assert.equal(opened,'app-1');
  assert.deepEqual(JSON.parse(calls.find(([url])=>url==='/api/finance/notifications/read')[1].body),{ids:[12]});
});
