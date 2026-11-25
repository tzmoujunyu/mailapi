const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'../../mailapi_shared/static');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const notice=(id,extra={})=>({id,kind:'quota',resource_key:'gpt:test',user_id:'',label:'资源 '+id,unread:true,created_at:'2026-11-24T10:00:00Z',...extra});

async function setup(t,items=[notice(1)]){
  const dom=new JSDOM('<html lang="zh-CN"><body><div data-test-account-id="admin"></div><div class="test-ui-header-actions"></div></body></html>',{url:'https://example.test/',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,calls=[],timers=[],navigations=[],state={items,error:false,backup:null,intercept:null};
  t.after(()=>w.close());
  w.setInterval=(callback,delay)=>{timers.push({callback,delay});return timers.length;};w.clearInterval=()=>{};
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.TestNavigation={navigate:async url=>navigations.push(url)};
  w.fetch=async(url,options={})=>{
    calls.push([url,options]);
    if(url==='/api/site-announcements')return{ok:true,json:async()=>({items:[1,2].map(id=>({id,title:'公告'+id,revision:1,updated_at:'2026-11-24',unread:true})),can_manage:false})};
    if(url.startsWith('/api/finance/notifications'))return{ok:true,json:async()=>({items:[],unread_count:3,total:0,max_id:0})};
    if(url==='/api/operations/notifications/read'){
      const payload=JSON.parse(options.body);
      for(const item of state.items)if(payload.ids?.includes(item.id)||item.id<=payload.through_id)item.unread=false;
      return{ok:true,json:async()=>({ok:true})};
    }
    if(state.intercept){const value=state.intercept(url,options);if(value)return value;}
    const page=Number(new URL(url,w.location).searchParams.get('page'));
    return{ok:!state.error,json:async()=>state.error?{detail:'访问已失效，请重新登录'}:{items:state.items.slice((page-1)*20,page*20),total:state.items.length,
      unread_count:state.items.filter(item=>item.unread).length,max_id:Math.max(0,...state.items.map(item=>item.id)),...(state.backup?{backup_status:state.backup}:{})}};
  };
  for(const file of ['operations-notices.js','test-notifications.js'])w.eval(fs.readFileSync(path.join(root,file),'utf8'));
  await tick();
  const section=w.document.querySelector('[data-notice-section="operations"]');
  return{w,state,calls,timers,navigations,section,find:selector=>section.querySelector(selector),badge:()=>w.document.querySelector('.notification-count').textContent};
}

test('combined badge keeps announcement and reimbursement unread counts when operations are marked read',async t=>{
  const app=await setup(t,[notice(1),notice(2)]);
  assert.equal(app.badge(),'7');
  assert.match(app.w.document.querySelector('[data-notice-tab="operations"]').textContent,/2/);
  app.find('[data-read-all]').click();await tick();
  assert.equal(app.badge(),'5');assert.equal(app.find('[data-read-all]').disabled,true);
  assert.deepEqual(JSON.parse(app.calls.find(([url])=>url==='/api/operations/notifications/read')[1].body),{through_id:2});
  assert.match(app.w.document.querySelector('[data-notice-tab="announcements"]').textContent,/2/);
  assert.match(app.w.document.querySelector('[data-notice-tab="finance"]').textContent,/3/);
});

test('periodic refresh updates the current page, clamps removed pages, and clears revoked content and badges',async t=>{
  const app=await setup(t,Array.from({length:41},(_,i)=>notice(i+1)));
  app.find('[data-next]').click();await tick();assert.equal(app.find('[data-page]').textContent,'2 / 3');
  const poll=app.timers.find(timer=>timer.delay===60000).callback;
  await poll();await tick();
  assert.match(app.calls.at(-1)[0],/operations\/notifications\?page=2/);
  app.state.items=[notice(1)];await poll();await tick();
  assert.equal(app.find('[data-page]').textContent,'1 / 1');assert.equal(app.find('[data-next]').disabled,true);
  assert.equal(app.find('[data-items]').querySelectorAll('article').length,1);
  app.state.error=true;app.find('[data-refresh]').click();await tick();
  assert.equal(app.find('[data-items]').querySelectorAll('article').length,0);assert.equal(app.badge(),'5');
  assert.equal(app.find('[data-read-all]').disabled,true);assert.match(app.find('[role="status"]').textContent,/访问已失效/);
});

test('late pagination responses cannot replace a newer explicit refresh',async t=>{
  const app=await setup(t,Array.from({length:25},(_,i)=>notice(i+1)));
  let resolve,signal;
  app.state.intercept=(url,options)=>url.endsWith('page=2')?new Promise(done=>{resolve=done;signal=options.signal;}):null;
  app.find('[data-next]').click();await tick();assert.ok(resolve);
  app.find('[data-refresh]').click();await tick();assert.equal(signal.aborted,true);
  resolve({ok:true,json:async()=>({items:[notice(99)],total:25,unread_count:25,max_id:99})});await tick();
  assert.equal(app.find('[data-page]').textContent,'1 / 2');assert.doesNotMatch(app.find('[data-items]').textContent,/资源 99/);
});

test('admin alerts never impersonate another API owner and dynamic resource labels stay plain text',async t=>{
  const app=await setup(t,[notice(1,{kind:'api_quota',user_id:'bob',label:'<img src=x onerror=alert(1)>'})]);
  assert.equal(app.section.querySelector('img'),null);
  app.find('[data-items] button').click();await tick();
  assert.equal(app.navigations.length,0);assert.match(app.find('[role="status"]').textContent,/为该用户调整额度/);
  app.state.items=[notice(2)];app.find('[data-refresh]').click();await tick();
  app.w.document.getElementById('header-notifications').click();app.find('[data-items] button').click();await tick();
  assert.deepEqual(app.navigations,['/']);assert.equal(app.w.document.getElementById('header-notice-panel').hidden,true);
  app.state.backup={available:false};app.find('[data-refresh]').click();await tick();
  assert.match(app.find('[data-backup]').textContent,/暂时无法读取/);assert.doesNotMatch(app.find('[data-backup]').textContent,/尚未配置/);
  app.state.backup={configured:true,fixture_only:true};app.find('[data-refresh]').click();await tick();
  assert.match(app.find('[data-backup]').textContent,/隔离样例备份演练/);
  assert.match(app.find('[data-backup]').textContent,/当前业务数据尚未启用定期备份/);
});
