const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const assets = path.resolve(__dirname,'../../mailapi_shared/static');
const read = name => fs.readFileSync(path.join(assets,name),'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function setup(t,handler) {
  const dom = new JSDOM(read('test-features.html'),{url:'http://localhost/test-features#view-api2api',runScripts:'outside-only'});
  t.after(() => dom.window.close());
  const w = dom.window, calls = [], el = id => w.document.getElementById(id);
  for (const dialog of w.document.querySelectorAll('dialog')) {
    dialog.showModal = () => dialog.setAttribute('open','');
    dialog.close = () => { dialog.removeAttribute('open'); dialog.dispatchEvent(new w.Event('close')); };
  }
  w.confirm = () => true;
  w.fetch = async (url,options = {}) => {
    calls.push([url,options]);
    const supplied = await handler?.(url,options);
    const defaults = {'/api/api2api/keys':{items:[]},'/api/api2api/engine':{engine:'newapi',linked:false,models:[]},'/api/api2api/usage':{requests:0,prompt_tokens:0,completion_tokens:0,total_tokens:0,items:[]}};
    return {ok:true,json:async () => supplied || defaults[url]};
  };
  w.eval(read('test-api.js')+'\nwindow.apiUnderTest = apiController;');
  w.apiUnderTest.start(false);
  const submit = id => el(id).dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
  return {w,el,calls,submit};
}

test('native key is shown once and cleared when its dialog closes; user never requests admin data',async t => {
  const key = 'sk-fixture-only-never-persist';
  const {w,el,calls,submit} = setup(t,(url,options) => options.method==='POST' ? {item:{id:'new-key',api_key:key}} : null);
  await w.apiUnderTest.load();
  el('api-open-key').click(); el('api-key-name').value='Development'; submit('api-key-form'); await tick();
  assert.equal(el('new-api-key').textContent,key);
  assert.equal(el('api-key-result').hidden,false);
  assert.equal(el('api-admin-tab').hidden,true);
  el('api-key-dialog').close();
  assert.equal(el('new-api-key').textContent,'');
  assert.equal(el('api-key-result').hidden,true);
  assert.ok(calls.every(([url]) => !url.startsWith('/api/admin/')));
  assert.equal(w.localStorage.length,0); assert.equal(w.sessionStorage.length,0);
});

test('closing during key creation never leaves a late key in the cached page and revokes the unclaimed key',async t => {
  let resolveCreation;
  const {el,calls,submit} = setup(t,(url,options) => options.method==='POST' ? new Promise(resolve => {resolveCreation=resolve;}) : {ok:true,items:[]});
  el('api-open-key').click(); el('api-key-name').value='Closing'; submit('api-key-form');
  el('api-key-dialog').close();
  resolveCreation({item:{id:'unclaimed',api_key:'sk-late-secret'}}); await tick();
  assert.equal(el('new-api-key').textContent,'');
  assert.ok(calls.some(([url,options]) => url==='/api/api2api/keys/unclaimed' && options.method==='DELETE'));
});

test('call filters apply to recent records and treat model names as plain text',async t => {
  const {w,el} = setup(t,url => url==='/api/api2api/usage' ? {requests:2,prompt_tokens:2,completion_tokens:3,total_tokens:5,items:[{created_at:'2026-11-20T00:00:00Z',model:'<img src=x onerror=alert(1)>',prompt_tokens:2,completion_tokens:3,total_tokens:5,request_ms:48,status_code:200},{created_at:'2026-11-20T00:00:00Z',model:'failed-model',prompt_tokens:0,completion_tokens:0,total_tokens:0,request_ms:20,status_code:503}]} : null);
  await w.apiUnderTest.load();
  assert.equal(el('api-usage-rows').querySelector('img'),null);
  el('api-log-status').value='error'; el('api-log-status').dispatchEvent(new w.Event('change'));
  assert.equal(el('api-usage-rows').children.length,1);
  assert.match(el('api-usage-rows').textContent,/failed-model/);
  assert.equal(el('api-metric-tokens').textContent,'5');
});

test('admin uses native management fields and clears the submitted secret',async t => {
  const {w,el,calls,submit} = setup(t,(url,options) => {
    if (options.method==='PUT') return {ok:true};
    if (url==='/api/admin/api2api/config') return {configured:true,engine:'newapi',base_url:'http://127.0.0.1:3001/v1',management_username:'independent',initial_quota:100,enabled:true};
    if (url==='/api/admin/api2api/usage' || url==='/api/admin/api2api/channels') return {items:[]};
    return null;
  });
  w.apiUnderTest.start(true); await w.apiUnderTest.loadAdmin();
  assert.equal(el('api-config-engine'),null);
  assert.equal(el('api-legacy-field'),null);
  el('api-open-config').click();
  assert.equal(el('api-config-dialog').open,true);
  el('api-config-password').value='test-management-secret'; submit('api-config-form'); await tick();
  const sent = JSON.parse(calls.find(([url,options]) => url==='/api/admin/api2api/config' && options.method==='PUT')[1].body);
  assert.equal(sent.engine,'newapi'); assert.equal(sent.management_password,'test-management-secret');
  assert.equal(el('api-config-password').value,'');
  assert.equal(el('api-config-dialog').open,false);
  assert.ok(!w.document.body.textContent.includes('test-management-secret'));
});

test('revocation uses a website dialog and waits for explicit confirmation',async t => {
  const {w,el,calls,submit} = setup(t,(url,options) => url==='/api/api2api/keys' ? {items:[{id:'one',name:'Preview key',provider:'newapi',enabled:true,revocable:true}]} : null);
  await w.apiUnderTest.load();
  el('api-key-list').querySelector('[data-revoke-key]').click();
  assert.equal(el('api-revoke-dialog').open,true);
  assert.ok(calls.every(([,options]) => options.method!=='DELETE'));
  submit('api-revoke-form'); await tick();
  assert.ok(calls.some(([url,options]) => url==='/api/api2api/keys/one' && options.method==='DELETE'));
  assert.equal(el('api-revoke-dialog').open,false);
});

test('key list copies on demand without retaining plaintext and pauses then resumes the same key',async t=>{
  const secret='sk-owned-key-for-copy-only';let enabled=true,copied='';
  const {w,el,calls}=setup(t,(url,options)=>{
    if(url==='/api/api2api/keys')return {items:[{id:'one',name:'Development',key_prefix:'sk-owned',provider:'newapi',enabled,revoked:false,available_here:true,can_copy:true,can_toggle:true,revocable:true}]};
    if(url.endsWith('/reveal'))return {api_key:secret};
    if(url.endsWith('/enabled')){enabled=JSON.parse(options.body).enabled;return {ok:true};}
  });
  Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{copied=value;}}});
  await w.apiUnderTest.load();
  el('api-key-list').querySelector('[data-copy-key]').click();await tick();
  assert.equal(copied,secret);assert.ok(!w.document.body.textContent.includes(secret));
  el('api-key-list').querySelector('[data-toggle-key]').click();await tick();
  assert.equal(enabled,false);assert.ok(el('api-key-list').textContent.includes('已停用'));
  assert.equal(el('api-key-list').querySelector('[data-toggle-key]').textContent,'启用');
  el('api-key-list').querySelector('[data-toggle-key]').click();await tick();
  assert.equal(enabled,true);assert.equal(el('api-key-list').querySelector('[data-toggle-key]').textContent,'停用');
  assert.deepEqual(calls.filter(([url])=>url.endsWith('/enabled')).map(([,options])=>JSON.parse(options.body).enabled),[false,true]);
  assert.equal(w.localStorage.length,0);assert.equal(w.sessionStorage.length,0);
});

test('manual copy fallback clears its full key on close and late copy results cannot open a hidden page',async t=>{
  let resolveCopy,delay=false;
  const {w,el}=setup(t,url=>{
    if(url==='/api/api2api/keys')return {items:[{id:'one',name:'Development',provider:'newapi',enabled:true,can_copy:true}]};
    if(url.endsWith('/reveal'))return delay?new Promise(resolve=>{resolveCopy=resolve;}):{api_key:'sk-manual-copy-fixture'};
  });
  await w.apiUnderTest.load();
  el('api-key-list').querySelector('[data-copy-key]').click();await tick();
  assert.equal(el('api-key-dialog').open,true);assert.equal(el('api-key-title').textContent,'复制 API 密钥');
  assert.equal(el('new-api-key').textContent,'sk-manual-copy-fixture');
  el('api-key-dialog').close();assert.equal(el('new-api-key').textContent,'');
  delay=true;el('api-key-list').querySelector('[data-copy-key]').click();await tick();
  w.location.hash='#view-finance';await tick();resolveCopy({api_key:'sk-late-copy-fixture'});await tick();
  assert.equal(el('api-key-dialog').open,false);assert.equal(el('new-api-key').textContent,'');
});

test('key search and state filters combine without changing total active keys',async t=>{
  const {w,el}=setup(t,url=>url==='/api/api2api/keys'?{items:[
    {id:'a',name:'Laptop',key_prefix:'sk-alpha',enabled:true,can_toggle:true},
    {id:'b',name:'Laptop paused',key_prefix:'sk-beta',enabled:false,can_toggle:true},
    {id:'c',name:'Archived',key_prefix:'sk-old',enabled:false,revoked:true,can_toggle:false},
  ]}:null);
  await w.apiUnderTest.load();
  el('api-key-filter').value='paused';el('api-key-filter').dispatchEvent(new w.Event('change'));
  assert.equal(el('api-key-list').querySelectorAll('tr').length,1);
  assert.match(el('api-key-list').textContent,/Laptop paused/);
  assert.equal(el('api-active-keys').textContent,'1');
  assert.equal(el('api-key-count').textContent,'1 / 3 条密钥');
  el('api-key-search').value='alpha';el('api-key-search').dispatchEvent(new w.Event('input'));
  assert.match(el('api-key-list').textContent,/没有符合条件/);
  el('api-key-filter').value='all';el('api-key-filter').dispatchEvent(new w.Event('change'));
  assert.match(el('api-key-list').textContent,/Laptop/);
  assert.ok(!el('api-key-list').textContent.includes('Laptop paused'));
});

test('admin user search matches notes and old connection data is not reused as a native gateway',async t=>{
  const {w,el}=setup(t,url=>{
    if(url==='/api/admin/api2api/config')return {configured:true,engine:'legacy',base_url:'https://old-upstream.invalid/v1',enabled:true};
    if(url==='/api/admin/api2api/usage')return {items:[{user_id:'a',username:'alice',user_note:'Agent A',requests:7},{user_id:'b',username:'bob',user_note:'Agent B',requests:3}]};
  });
  w.apiUnderTest.start(true);await w.apiUnderTest.loadAdmin();
  assert.equal(el('api-config-url').value,'');
  assert.equal(el('api-open-channel').disabled,true);
  el('api-user-search').value='agent b';el('api-user-search').dispatchEvent(new w.Event('input'));
  assert.equal(el('admin-api-usage-rows').querySelectorAll('tr').length,1);
  assert.match(el('admin-api-usage-rows').textContent,/bob/);
  assert.equal(el('api-user-count').textContent,'1 / 2 位用户');
  assert.equal(el('admin-api-usage-rows').querySelector('button').disabled,true);
});
