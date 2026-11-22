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
    dialog.show = () => dialog.setAttribute('open','');
    dialog.close = () => { dialog.removeAttribute('open'); dialog.dispatchEvent(new w.Event('close')); };
  }
  w.confirm = () => true;
  w.fetch = async (url,options = {}) => {
    calls.push([url,options]);
    const supplied = await handler?.(url,options);
    const defaults = {'/api/api2api/keys':{items:[]},'/api/api2api/engine':{engine:'newapi',linked:false,models:[]},'/api/api2api/usage':{requests:0,prompt_tokens:0,completion_tokens:0,total_tokens:0,items:[]}};
    return {ok:true,json:async () => supplied || defaults[url]};
  };
  w.eval(read('test-api-models.js')+'\n'+read('test-api-records.js')+'\n'+read('test-api.js')+'\nwindow.apiUnderTest = apiController;');
  w.apiUnderTest.start(false);
  const submit = id => el(id).dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
  return {w,el,calls,submit};
}

test('native key is shown once and cleared when its dialog closes; user never requests admin data',async t => {
  const key = 'sk-fixture-only-never-persist';
  const {w,el,calls,submit} = setup(t,(url,options) => options.method==='POST' ? {item:{id:'new-key',api_key:key}} : null);
  await w.apiUnderTest.load();
  el('api-open-key').click(); el('api-limits-name').value='Development'; submit('api-limits-form'); await tick();
  assert.equal(el('new-api-key').textContent,key);
  assert.equal(el('api-key-result').hidden,false);
  assert.equal(el('api-admin-tab').hidden,true);
  el('api-limits-dialog').close();
  assert.equal(el('new-api-key').textContent,'');
  assert.equal(el('api-key-result').hidden,true);
  assert.ok(calls.every(([url]) => !url.startsWith('/api/admin/')));
  assert.equal(w.localStorage.length,0); assert.equal(w.sessionStorage.length,0);
});

test('closing during key creation never leaves a late key in the cached page and revokes the unclaimed key',async t => {
  let resolveCreation;
  const {el,calls,submit} = setup(t,(url,options) => options.method==='POST' ? new Promise(resolve => {resolveCreation=resolve;}) : {ok:true,items:[]});
  el('api-open-key').click(); el('api-limits-name').value='Closing'; submit('api-limits-form');
  el('api-limits-dialog').close();
  resolveCreation({item:{id:'unclaimed',api_key:'sk-late-secret'}}); await tick();
  assert.equal(el('new-api-key').textContent,'');
  assert.ok(calls.some(([url,options]) => url==='/api/api2api/keys/unclaimed' && options.method==='DELETE'));
});

test('creation shares the editor fields and sends restrictions in the initial create request',async t=>{
  let copied='';
  const {w,el,calls,submit}=setup(t,(url,options)=>url==='/api/api2api/keys'&&options.method==='POST'?{item:{id:'new',api_key:'sk-configured-create-fixture'}}:url==='/api/api2api/engine'?{models:['gpt-4o-mini']} : null);
  Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{copied=value;}}});
  await w.apiUnderTest.load();el('api-open-key').click();
  assert.equal(el('api-limits-title').textContent,'创建 API 密钥');assert.equal(el('api-limits-save').textContent,'创建密钥');
  assert.equal(el('api-limits-save').disabled,true);assert.equal(el('api-limits-never').checked,true);
  assert.equal(el('api-limits-unlimited').checked,true);assert.equal(el('api-limits-query-quota').checked,true);
  el('api-limits-name').value='Development';el('api-limits-name').dispatchEvent(new w.Event('input',{bubbles:true}));
  assert.equal(el('api-limits-save').disabled,false);
  el('api-limits-unlimited').click();el('api-limits-quota').value='120';el('api-limits-query-quota').click();
  el('api-limits-ips').value='192.0.2.5';el('api-limits-form').querySelector('[data-key-expiry="hour"]').click();
  el('api-limits-model-options').querySelector('input').click();submit('api-limits-form');await tick();
  const sent=JSON.parse(calls.find(([url,options])=>url==='/api/api2api/keys'&&options.method==='POST')[1].body);
  assert.equal(sent.name,'Development');assert.equal(sent.unlimited_quota,false);assert.equal(sent.remaining_quota,120);
  assert.equal(sent.allow_user_quota_query,false);assert.equal(sent.allowed_ips,'192.0.2.5');assert.deepEqual(sent.models,['gpt-4o-mini']);
  assert.ok(Math.abs(sent.expired_at-Date.now()/1000-3600)<3);assert.equal(sent.group,undefined);
  assert.ok(calls.every(([url,options])=>!url.endsWith('/limits')||options.method!=='PUT'));
  assert.equal(el('api-limits-dialog').open,true);assert.equal(el('api-limits-form').hidden,true);assert.equal(el('api-key-result').hidden,false);
  assert.equal(w.document.activeElement,el('api-copy-key'));
  el('api-copy-key').click();await tick();assert.equal(copied,'sk-configured-create-fixture');
  el('api-limits-dialog').close();assert.equal(el('new-api-key').textContent,'');
  el('api-open-key').click();assert.equal(el('api-limits-name').value,'');assert.equal(el('api-limits-ips').value,'');
  assert.equal(el('api-limits-models').value,'');assert.equal(el('api-limits-never').checked,true);assert.equal(el('api-limits-unlimited').checked,true);
  assert.equal(el('api-limits-query-quota').checked,true);assert.equal(el('api-limits-form').hidden,false);
});

test('creation errors keep the entered settings and enable a corrected retry',async t=>{
  const {w,el,submit}=setup(t);
  el('api-open-key').click();el('api-limits-name').value='Development';el('api-limits-ips').value='invalid-address';
  w.fetch=async()=>({ok:false,status:400,json:async()=>({detail:'Invalid IP whitelist'})});
  submit('api-limits-form');await tick();
  assert.equal(el('api-limits-dialog').open,true);assert.equal(el('api-limits-name').value,'Development');
  assert.equal(el('api-limits-ips').value,'invalid-address');assert.equal(el('api-limits-status').textContent,'Invalid IP whitelist');
  assert.equal(el('api-limits-save').disabled,false);assert.equal(el('api-key-result').hidden,true);
});

test('duplicate creation is blocked and a closed request cannot replace a reopened creation result',async t=>{
  const pending=[];
  const {el,calls,submit}=setup(t,(url,options)=>url==='/api/api2api/keys'&&options.method==='POST'?new Promise(resolve=>pending.push(resolve)):null);
  el('api-open-key').click();el('api-limits-name').value='First';submit('api-limits-form');submit('api-limits-form');
  assert.equal(pending.length,1);el('api-limits-dialog').close();el('api-open-key').click();
  el('api-limits-name').value='Second';submit('api-limits-form');
  pending[0]({item:{id:'old',api_key:'sk-old-create-fixture'}});await tick();
  assert.equal(el('api-limits-save').disabled,true);assert.equal(el('api-limits-name').value,'Second');
  assert.equal(el('new-api-key').textContent,'');assert.ok(calls.some(([url,options])=>url==='/api/api2api/keys/old'&&options.method==='DELETE'));
  pending[1]({item:{id:'new',api_key:'sk-new-create-fixture'}});await tick();
  assert.equal(el('new-api-key').textContent,'sk-new-create-fixture');assert.equal(el('api-key-result').hidden,false);
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
  el('api-key-list').querySelector('[data-more-key]').click();
  el('api-menu-revoke').click();
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
  el('api-key-list').querySelector('[data-more-key]').click();el('api-key-menu').querySelector('[data-copy-key]').click();await tick();
  assert.equal(copied,secret);assert.ok(!w.document.body.textContent.includes(secret));
  el('api-key-list').querySelector('[data-toggle-key]').click();await tick();
  assert.equal(enabled,false);assert.ok(el('api-key-list').textContent.includes('已停用'));
  assert.equal(el('api-key-list').querySelector('[data-toggle-key]').getAttribute('aria-label'),'启用 Development');
  el('api-key-list').querySelector('[data-toggle-key]').click();await tick();
  assert.equal(enabled,true);assert.equal(el('api-key-list').querySelector('[data-toggle-key]').getAttribute('aria-label'),'停用 Development');
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
  el('api-key-list').querySelector('[data-more-key]').click();el('api-key-menu').querySelector('[data-copy-key]').click();await tick();
  assert.equal(el('api-limits-dialog').open,true);assert.equal(el('api-limits-title').textContent,'复制 API 密钥');
  assert.equal(el('new-api-key').textContent,'sk-manual-copy-fixture');
  el('api-limits-dialog').close();assert.equal(el('new-api-key').textContent,'');
  delay=true;el('api-key-list').querySelector('[data-more-key]').click();el('api-key-menu').querySelector('[data-copy-key]').click();await tick();
  w.location.hash='#view-finance';await tick();resolveCopy({api_key:'sk-late-copy-fixture'});await tick();
  assert.equal(el('api-limits-dialog').open,false);assert.equal(el('new-api-key').textContent,'');
});

test('a delayed clipboard failure after navigation cannot reopen a drawer containing a key',async t=>{
  let rejectCopy;
  const {w,el}=setup(t,url=>url==='/api/api2api/keys'?{items:[{id:'one',name:'Development',can_copy:true}]}:url.endsWith('/reveal')?{api_key:'sk-delayed-copy-fixture'}:null);
  Object.defineProperty(w.navigator,'clipboard',{value:{writeText:()=>new Promise((resolve,reject)=>{rejectCopy=reject;})}});
  await w.apiUnderTest.load();el('api-key-list').querySelector('[data-copy-key]').click();await tick();
  w.location.hash='#view-finance';await tick();rejectCopy(new Error('Clipboard denied'));await tick();
  assert.equal(el('api-limits-dialog').open,false);assert.equal(el('new-api-key').textContent,'');
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

test('key restriction editor sends personal IP rules with model, expiry and revision fields',async t=>{
  const {w,el,submit,calls}=setup(t,url=>url==='/api/api2api/keys'?{items:[{
    id:'one',name:'Laptop " <img src=x onerror=alert(1)>',key_prefix:'sk-prefix',enabled:true,
    can_toggle:true,can_edit:true,model_limits:'gpt-4o-mini',expired_at:-1,limits_revision:'revision-1',allowed_ips:'192.0.2.5',gateway_allowed_ips:'127.0.0.1',
  }]}:{ok:true});
  await w.apiUnderTest.load();
  assert.equal(el('api-key-list').querySelector('img'),null);
  el('api-key-list').querySelector('[data-edit-key]').click();
  assert.equal(el('api-limits-dialog').open,true);
  assert.equal(el('api-limits-never').checked,true);
  assert.equal(el('api-limits-time').disabled,true);
  assert.equal(el('api-limits-ips').value,'192.0.2.5');
  assert.equal(el('api-limits-gateway-ips').hidden,false);
  el('api-limits-ips').value='192.0.2.5\n2001:db8::/32';
  el('api-limits-models').value='gpt-4o-mini,\nmodel-two';submit('api-limits-form');await tick();
  const sent=calls.find(([url,options])=>url.endsWith('/limits') && options.method==='PUT');
  assert.deepEqual(JSON.parse(sent[1].body),{name:'Laptop " <img src=x onerror=alert(1)>',models:['gpt-4o-mini','model-two'],expired_at:-1,allowed_ips:'192.0.2.5\n2001:db8::/32',unlimited_quota:true,allow_user_quota_query:true,revision:'revision-1'});
  assert.equal(el('api-limits-dialog').open,false);
});

test('invalid IP rules keep the editor open without silently clearing the saved policy',async t=>{
  const {w,el,submit}=setup(t,url=>url==='/api/api2api/keys'?{items:[{id:'one',name:'Laptop',can_edit:true,allowed_ips:'192.0.2.5',expired_at:-1,limits_revision:'one'}]}:{ok:true});
  await w.apiUnderTest.load();
  el('api-key-list').querySelector('[data-edit-key]').click();
  el('api-limits-ips').value='example.com';
  w.fetch=async()=>({ok:false,status:400,json:async()=>({detail:'Invalid IP whitelist'})});
  submit('api-limits-form');await tick();
  assert.equal(el('api-limits-dialog').open,true);
  assert.equal(el('api-limits-ips').value,'example.com');
  assert.equal(el('api-limits-status').textContent,'Invalid IP whitelist');
  el('api-limits-dialog').close();
  assert.equal(el('api-limits-ips').value,'');
});

test('inline copy and connection copy resolve the same owned key without retaining credentials',async t=>{
  let copied='';const secret='sk-connection-copy-fixture';
  const {w,el,calls}=setup(t,url=>url==='/api/api2api/keys'?{items:[{id:'one',name:'Laptop',key_prefix:'sk-fixture',can_copy:true,revocable:true}]}:url.endsWith('/reveal')?{api_key:secret}:null);
  Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{copied=value;}}});
  await w.apiUnderTest.load();
  const inline=el('api-key-list').querySelector('.api-key-secret-line [data-copy-key]');
  assert.equal(inline.title,'复制密钥');inline.click();await tick();
  assert.equal(copied,secret);
  assert.ok(inline.classList.contains('is-copied'));
  el('api-key-list').querySelector('[data-more-key]').click();el('api-menu-connection').click();await tick();
  assert.deepEqual(JSON.parse(copied),{_type:'newapi_channel_conn',key:secret,url:'http://localhost/api2api'});
  assert.equal(el('api-key-menu').open,false);
  assert.equal(el('api-key-status').textContent,'连接信息已复制');
  assert.equal(calls.filter(([url])=>url.endsWith('/reveal')).length,2);
  assert.ok(!w.document.body.textContent.includes(secret));assert.equal(w.localStorage.length,0);assert.equal(w.sessionStorage.length,0);
});

test('connection copy fallback names the copied content correctly and clears it on close',async t=>{
  const {el}=setup(t,url=>url==='/api/api2api/keys'?{items:[{id:'one',name:'Laptop',can_copy:true}]}:url.endsWith('/reveal')?{api_key:'sk-connection-fallback-fixture'}:null);
  await tick();el('api-refresh').click();await tick();
  el('api-key-list').querySelector('[data-more-key]').click();el('api-menu-connection').click();await tick();
  assert.equal(el('api-limits-title').textContent,'复制连接信息');assert.equal(el('api-copy-key').textContent,'复制连接信息');
  assert.equal(JSON.parse(el('new-api-key').textContent)._type,'newapi_channel_conn');
  el('api-limits-dialog').close();assert.equal(el('new-api-key').textContent,'');assert.equal(el('api-copy-key').textContent,'复制密钥');
});

test('more menu supports arrows, Escape and outside dismissal without blocking navigation',async t=>{
  const {w,el}=setup(t,url=>url==='/api/api2api/keys'?{items:[{id:'one',name:'Laptop',can_copy:true,revocable:true}]}:null);
  await w.apiUnderTest.load();const trigger=el('api-key-list').querySelector('[data-more-key]');
  trigger.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));
  assert.equal(trigger.getAttribute('aria-expanded'),'true');assert.equal(w.document.activeElement,el('api-menu-copy'));
  el('api-key-menu').dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));assert.equal(w.document.activeElement,el('api-menu-connection'));
  el('api-key-menu').dispatchEvent(new w.KeyboardEvent('keydown',{key:'End',bubbles:true}));assert.equal(w.document.activeElement,el('api-menu-revoke'));
  el('api-key-menu').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(el('api-key-menu').open,false);assert.equal(w.document.activeElement,trigger);
  trigger.click();el('api-refresh').dispatchEvent(new w.MouseEvent('pointerdown',{bubbles:true}));assert.equal(el('api-key-menu').open,false);assert.equal(trigger.getAttribute('aria-expanded'),'false');
});

test('full editor tracks changes and saves name, quota, query permission, presets and model choices',async t=>{
  const {w,el,calls,submit}=setup(t,url=>url==='/api/api2api/keys'?{items:[{id:'one',name:'Laptop',can_edit:true,expired_at:-1,unlimited:true,remaining_quota:90,allow_user_quota_query:true,limits_revision:'first'}]}:url==='/api/api2api/engine'?{models:['gpt-4o-mini','model-two']}:{ok:true});
  await w.apiUnderTest.load();el('api-key-list').querySelector('[data-edit-key]').click();
  assert.equal(el('api-limits-save').disabled,true);assert.equal(el('api-limits-quota-field').hidden,true);
  el('api-limits-name').value='Laptop renamed';el('api-limits-name').dispatchEvent(new w.Event('input',{bubbles:true}));assert.equal(el('api-limits-save').disabled,false);
  el('api-limits-unlimited').click();assert.equal(el('api-limits-quota-field').hidden,false);el('api-limits-quota').value='55';
  el('api-limits-query-quota').click();
  el('api-limits-form').querySelector('[data-key-expiry="hour"]').click();assert.equal(el('api-limits-time').disabled,false);
  el('api-limits-model-options').querySelector('input').click();
  assert.equal(el('api-limits-models').value,'gpt-4o-mini');assert.equal(w.document.activeElement.dataset.editModel,'gpt-4o-mini');
  submit('api-limits-form');await tick();
  const sent=JSON.parse(calls.find(([url,options])=>url.endsWith('/limits') && options.method==='PUT')[1].body);
  assert.equal(sent.name,'Laptop renamed');assert.equal(sent.remaining_quota,55);assert.equal(sent.expected_remaining_quota,90);
  assert.equal(sent.unlimited_quota,false);assert.equal(sent.allow_user_quota_query,false);assert.deepEqual(sent.models,['gpt-4o-mini']);
  assert.ok(Math.abs(sent.expired_at-Date.now()/1000-3600)<3);assert.equal(sent.group,undefined);
});

test('a late save from a closed editor cannot unlock or close a newer pending edit',async t=>{
  const pending=[];
  const {w,el,submit}=setup(t,(url,options)=>url==='/api/api2api/keys'?{items:[{id:'one',name:'Laptop',can_edit:true,expired_at:-1,limits_revision:'first'}]}:url.endsWith('/limits')&&options.method==='PUT'?new Promise(resolve=>pending.push(resolve)):{ok:true});
  await w.apiUnderTest.load();el('api-key-list').querySelector('[data-edit-key]').click();
  el('api-limits-name').value='First edit';submit('api-limits-form');el('api-limits-dialog').close();
  el('api-key-list').querySelector('[data-edit-key]').click();el('api-limits-name').value='Second edit';submit('api-limits-form');
  pending[0]({ok:true});await tick();
  assert.equal(el('api-limits-dialog').open,true);assert.equal(el('api-limits-name').value,'Second edit');assert.equal(el('api-limits-save').disabled,true);
  pending[1]({ok:true});await tick();assert.equal(el('api-limits-dialog').open,false);
});

test('the expiration calendar supports keyboard dates and closes without losing the separate time field',async t=>{
  const {w,el}=setup(t,url=>url==='/api/api2api/keys'?{items:[{id:'one',name:'Laptop',can_edit:true,expired_at:-1}]}:{ok:true});
  await w.apiUnderTest.load();el('api-key-list').querySelector('[data-edit-key]').click();
  el('api-limits-calendar').open=true;
  const today=el('api-key-calendar-days').querySelector('button[tabindex="0"]');today.focus();
  today.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true}));
  const selected=w.document.activeElement.dataset.keyDate;assert.ok(selected>today.dataset.keyDate);
  w.document.activeElement.click();
  assert.equal(el('api-limits-calendar').open,false);assert.equal(el('api-limits-never').checked,false);
  assert.match(el('api-limits-expires').value,new RegExp('^'+selected+'T'));
  el('api-limits-time').value='18:30:00';el('api-limits-time').dispatchEvent(new w.Event('input',{bubbles:true}));
  assert.equal(el('api-limits-expires').value,selected+'T18:30:00');assert.equal(el('api-limits-save').disabled,false);
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
