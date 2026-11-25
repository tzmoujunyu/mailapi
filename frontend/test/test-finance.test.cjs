const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const assets = path.resolve(__dirname, '../../mailapi_shared/static');
const read = name => fs.readFileSync(path.join(assets,name),'utf8');
const tick = () => new Promise(resolve=>setImmediate(resolve));
const application = (extra={}) => ({id:'app-1', username:'alice', user_note:'Agent', project:'模型订阅', amount:'12.50', amount_cents:1250, version:1, groups:['A','B'], expense_group_id:'a', expense_group_name:'A', status:'pending', created_at:new Date().toISOString(), files:[{kind:'invoice',original_name:'Invoice.pdf'}], ...extra});
async function setup(t, {admin=false, richEditor=false, preferences, mine=[], all=[], history=[], groups=[], payers=[], announcements=[{id:'notice',title:'公告<1>',body:'请补齐材料',pinned:true}]}={}) {
  const dom=new JSDOM(read('test-features.html'),{url:'http://localhost/test-features#view-finance',runScripts:'outside-only'});
  t.after(()=>{dom.window.FinanceAnnouncementEditor?.destroy();dom.window.close();});const w=dom.window;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
  w.confirm=()=>true;
  w.scrollTo=()=>{};
  w.ViewPreferences=preferences;
  if(richEditor){
    w.matchMedia=()=>({matches:true});w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
    w.Range.prototype.getClientRects=()=>[];w.Range.prototype.getBoundingClientRect=()=>({left:0,top:0,right:0,bottom:0,width:0,height:0});
    for(const name of ['vendor/tiptap/tiptap.js','codex-tip-content.js','codex-tip-format.js','shared-content-editor.js','shared-content-dialog.js','shared-announcement-editor.js'])w.eval(read(name));
  }
  const requests=[];
  const fixtures={'/api/finance/applications':{items:mine},'/api/finance/announcements':{items:announcements},'/api/finance/groups':{items:groups},'/api/admin/finance/applications':{items:all},'/api/admin/finance/history':{items:history},'/api/admin/finance/payers':{items:payers}};
  w.fetch=async(url,options={})=>{requests.push([url,options]);return {ok:true,json:async()=>fixtures[url] || {items:[]}};};
  w.eval(read('test-finance.js')+`\nwindow.financeFixtureController=financeController;`);
  w.financeFixtureController.start(admin,'admin');await w.financeFixtureController.load();
  return {w,el:id=>w.document.getElementById(id),requests};
}
test('finance preferences restore custom controls and reset persists the tab defaults without search text',async t=>{
  const writes=[],reads=[];
  const {w,el}=await setup(t,{admin:true,all:[application()],preferences:{
    read:async scope=>{reads.push(scope);return {period:'custom',from:'2026-01-01',to:'2026-12-31',status:'all',min:'1.00'};},
    save:(scope,settings)=>writes.push([scope,settings]),
  }});
  await tick();
  assert.equal(el('fin-period').value,'custom');assert.equal(el('fin-custom-range').hidden,false);
  assert.equal(el('fin-from').disabled,false);assert.equal(el('fin-from').value,'2026-01-01');
  el('fin-search').value='private description';el('fin-search').dispatchEvent(new w.Event('input'));
  assert.equal(writes.length,0);
  el('fin-reset').click();await tick();
  assert.equal(el('fin-status').value,'pending');assert.equal(el('fin-period').value,'all');
  assert.equal(el('fin-from').value,'');assert.equal(el('fin-search').value,'');
  assert.equal(writes.at(-1)[0],'finance-review');assert.equal(writes.at(-1)[1].status,'pending');
  assert.ok(!JSON.stringify(writes).includes('private description'));
  assert.equal(reads.length,1);
});

test('finance relative periods recalculate today and late preferences cannot replace a search draft',async t=>{
  const {el}=await setup(t,{admin:true,preferences:{read:async()=>({period:'month',from:'2000-01-01',to:'2000-01-02'}),save:()=>{}}});
  await tick();
  const today=new Date(),month=`${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}`;
  assert.equal(el('fin-from').value,month+'-01');
  assert.equal(el('fin-to').value,month+'-'+String(today.getDate()).padStart(2,'0'));
  let resolve;
  const second=await setup(t,{admin:true,preferences:{read:()=>new Promise(done=>{resolve=done;}),save:()=>{}}});
  second.el('fin-search').value='editing';second.el('fin-search').dispatchEvent(new second.w.Event('input'));
  resolve({status:'approved',period:'custom',from:'2020-01-01',to:'2020-01-31'});await tick();
  assert.equal(second.el('fin-status').value,'pending');assert.equal(second.el('fin-period').value,'all');
  assert.equal(second.el('fin-search').value,'editing');
});

test('closing application details returns to the original list position',async t=>{
  const item=application(),{w,el}=await setup(t,{admin:true,all:[item]});
  const fetch=w.fetch,scroll=[];w.scrollTo=options=>scroll.push(options);
  w.fetch=async(url,options)=>url.endsWith('/app-1/history')?{ok:true,json:async()=>({item,items:[]})}:fetch(url,options);
  w.scrollY=480;el('admin-finance-rows').querySelector('[data-history]').click();await tick();
  w.scrollY=0;el('fin-detail-dialog').close();
  assert.equal(scroll.at(-1).top,480);
});

test('application notes can be edited and cleared and do not leak into a new application',async t=>{
  const item=application({status:'rejected',application_note:'上次备注\n保留换行'});
  const {w,el,requests}=await setup(t,{mine:[item]});
  w.document.querySelector('[data-edit-finance]').click();
  assert.equal(el('finance-note').value,item.application_note);
  assert.equal(el('finance-note').required,false);assert.equal(el('finance-note').maxLength,500);
  el('finance-note').value='修改后的用途\n第二行';
  el('finance-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.equal(requests.find(([,options])=>options.method==='PUT')[1].body.get('application_note'),'修改后的用途\n第二行');
  w.document.querySelector('[data-edit-finance]').click();el('finance-note').value='';
  el('finance-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.equal(requests.filter(([,options])=>options.method==='PUT').at(-1)[1].body.get('application_note'),'');
  el('finance-open-form').click();assert.equal(el('finance-note').value,'');
});
test('notes only appear in details and version history as escaped plain text',async t=>{
  const note='用途说明\n<img src=x onerror="alert(1)">',item=application({application_note:note});
  const {w,el,requests}=await setup(t,{admin:true,all:[item]});
  assert.ok(!el('admin-finance-rows').textContent.includes('用途说明'));
  const original=w.fetch;
  w.fetch=async(url,options)=>url==='/api/finance/applications/app-1/history'
    ?(requests.push([url,options]),{ok:true,json:async()=>({item,items:[{id:'event-1',version:1,action:'submitted',actor_name:'alice',created_at:item.created_at,snapshot:application({application_note:'历史备注\n旧用途'})}]})})
    :original(url,options);
  w.document.querySelector('[data-history]').click();await tick();
  assert.equal(el('fin-detail-dialog').classList.contains('fin-application-detail'),true);
  assert.ok(el('fin-detail-content').querySelector('.fin-detail-information .fin-detail-summary'));
  assert.ok(el('fin-detail-content').querySelector('.fin-detail-information .fin-detail-history .fin-event'));
  assert.equal(el('fin-detail-previews').closest('.fin-detail-information'),null);
  const notes=el('fin-detail-content').querySelectorAll('.fin-application-note .fin-prewrap');
  assert.equal(notes.length,2);assert.equal(notes[0].textContent,note);assert.equal(notes[1].textContent,'历史备注\n旧用途');
  assert.equal(el('fin-detail-content').querySelector('img'),null);
  el('fin-detail-dialog').close();
  w.fetch=async(url,options)=>url==='/api/finance/applications/app-1/history'
    ?{ok:true,json:async()=>({item:application(),items:[{id:'legacy',version:1,action:'submitted',actor_name:'alice',snapshot:application()}]})}:original(url,options);
  w.document.querySelector('[data-history]').click();await tick();
  assert.equal(el('fin-detail-content').querySelectorAll('.fin-application-note').length,0);
});
test('admin reimbursement menu names the current payer and supports keyboard dismissal without affecting ordinary submission',async t=>{
  const {w,el,requests}=await setup(t,{admin:true,groups:[{id:'own',name:'管理员组'}]});
  el('finance-open-form').click();
  assert.equal(el('finance-dialog').open,false);assert.equal(el('fin-apply-options').hidden,false);
  assert.match(el('fin-apply-self').textContent,/admin/);
  el('fin-apply-self').dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true,cancelable:true}));
  assert.equal(w.document.activeElement,el('fin-apply-other'));
  el('fin-apply-other').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  assert.equal(el('fin-apply-options').hidden,true);assert.equal(w.document.activeElement,el('finance-open-form'));
  el('finance-open-form').click();el('fin-apply-self').click();
  assert.equal(el('finance-dialog').open,true);assert.equal(el('fin-payer-picker').hidden,true);
  assert.equal(el('fin-expense-group').value,'own');
  assert.ok(!requests.some(([url])=>url==='/api/admin/finance/payers'));
  const user=await setup(t);user.el('finance-open-form').click();
  assert.equal(user.el('finance-dialog').open,true);assert.equal(user.el('fin-apply-options').hidden,true);
  assert.equal(user.el('finance-open-form').hasAttribute('aria-haspopup'),false);
});
test('proxy submission searches payers, uses their groups and sends an explicit owner rather than the administrator',async t=>{
  const {w,el,requests}=await setup(t,{admin:true,groups:[{id:'own',name:'管理员组'}],payers:[
    {id:'alice',username:'alice',note:'财务组',groups:[{id:'a',name:'A'},{id:'b',name:'B'}]},
    {id:'bob',username:'bob',note:'研发组',groups:[{id:'c',name:'C'}]},
  ]});
  el('finance-open-form').click();el('fin-apply-other').click();await tick();
  assert.equal(el('fin-payer-picker').hidden,false);assert.equal(el('fin-payer-user').required,true);
  el('finance-project').value='代提交订阅';
  el('finance-note').value='管理员填写的补充说明';
  el('finance-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.match(el('fin-form-status').textContent,/选择付款人/);
  assert.ok(!requests.some(([,o])=>o.method==='POST'));
  el('fin-payer-user').value='alice';el('fin-payer-user').dispatchEvent(new w.Event('change'));
  assert.equal(el('fin-expense-label').hidden,false);assert.equal(el('fin-expense-group').required,true);
  assert.ok(![...el('fin-expense-group').options].some(o=>o.value==='own'));
  el('fin-payer-search').value='研发';el('fin-payer-search').dispatchEvent(new w.Event('input'));
  assert.equal(el('fin-payer-user').value,'');assert.equal(el('fin-expense-group').value,'');
  el('fin-payer-user').value='bob';el('fin-payer-user').dispatchEvent(new w.Event('change'));
  assert.equal(el('fin-expense-group').value,'c');assert.equal(el('fin-expense-label').hidden,true);
  w.FinanceInvoices={getFields:()=>({number:'20261122000000000001',code:'',date:'2026-11-22',total:'123.45',check_code:''})};
  Object.defineProperty(el('finance-invoice'),'files',{value:[new w.File(['invoice'],'invoice.pdf',{type:'application/pdf'})]});
  el('finance-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  const [url,options]=requests.find(([url,o])=>url==='/api/admin/finance/applications'&&o.method==='POST');
  assert.equal(url,'/api/admin/finance/applications');assert.equal(options.body.get('payer_user_id'),'bob');
  assert.equal(options.body.get('application_note'),'管理员填写的补充说明');
  assert.equal(options.body.get('expense_group_id'),'c');assert.equal(el('finance-dialog').open,false);
});
test('late payer lists do not change a closed or personal application and failed lists cannot silently submit as self',async t=>{
  const {w,el,requests}=await setup(t,{admin:true,groups:[{id:'own',name:'自己的组'}]});
  const original=w.fetch;let resolve;
  w.fetch=(url,options)=>url==='/api/admin/finance/payers'?new Promise(done=>{resolve=done;}):original(url,options);
  el('finance-open-form').click();el('fin-apply-other').click();el('finance-dialog').close();
  el('finance-open-form').click();el('fin-apply-self').click();
  resolve({ok:true,json:async()=>({items:[{id:'late',username:'late',groups:[]}]})});await tick();
  assert.equal(el('fin-payer-picker').hidden,true);assert.equal(el('fin-expense-group').value,'own');
  assert.ok(!el('fin-payer-user').textContent.includes('late'));el('finance-dialog').close();
  w.fetch=async(url,options)=>url==='/api/admin/finance/payers'?{ok:false,json:async()=>({detail:'账户加载失败'})}:original(url,options);
  el('finance-open-form').click();el('fin-apply-other').click();await tick();
  assert.match(el('fin-payer-status').textContent,/加载失败/);assert.equal(el('fin-payer-user').disabled,true);
  el('finance-project').value='不能误提交';el('finance-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.ok(!requests.some(([,o])=>o.method==='POST'));
});
test('an unavailable delegated endpoint keeps the chosen payer and never falls back to submitting as the administrator',async t=>{
  const {w,el,requests}=await setup(t,{admin:true,payers:[{id:'alice',username:'alice',note:'',groups:[]}]});
  const original=w.fetch;
  w.fetch=async(url,options={})=>{if(url==='/api/admin/finance/applications'&&options.method==='POST'){requests.push([url,options]);return {ok:false,json:async()=>({detail:'代提交接口暂不可用'})};}return original(url,options);};
  el('finance-open-form').click();el('fin-apply-other').click();await tick();
  el('fin-payer-user').value='alice';el('fin-payer-user').dispatchEvent(new w.Event('change'));
  el('finance-project').value='保留付款人';el('finance-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.equal(el('finance-dialog').open,true);assert.equal(el('fin-payer-user').value,'alice');
  assert.match(el('fin-form-status').textContent,/暂不可用/);
  assert.ok(!requests.some(([url,o])=>url==='/api/finance/applications'&&o.method==='POST'));
});
test('delegated applications show escaped actor names beside the payer and remain visible in the owner list',async t=>{
  const item=application({submitted_on_behalf:true,submitted_by_name:'<img src=x onerror=alert(1)>'});
  const {el}=await setup(t,{admin:true,all:[item]});
  assert.match(el('admin-finance-rows').querySelector('.fin-delegate').textContent,/<img src=x/);
  assert.equal(el('admin-finance-rows').querySelector('.fin-delegate img'),null);
  const user=await setup(t,{mine:[item]});
  assert.match(user.el('finance-rows').querySelector('.fin-delegate').textContent,/代替申请/);
});
test('finance announcements use the shared expandable editor and protect unsaved changes',async t=>{
  const {w,el}=await setup(t,{admin:true,richEditor:true});
  el('fin-new-notice').click();await tick();
  const dialog=el('finance-notice-edit-dialog');assert.ok(dialog.open);
  assert.ok(dialog.querySelector('[data-tip-action="image"]'));assert.ok(dialog.querySelector('[data-tip-mode="preview"]'));
  el('finance-notice-expand').click();assert.ok(dialog.classList.contains('is-expanded'));
  el('finance-notice-title').value='尚未保存';el('finance-notice-title').dispatchEvent(new w.Event('input',{bubbles:true}));
  el('finance-notice-cancel').click();await tick();assert.equal(dialog.open,true);assert.equal(el('finance-notice-leave-warning').hidden,false);
  el('finance-notice-keep-editing').click();await tick();assert.equal(dialog.open,true);
  el('finance-notice-cancel').click();await tick();el('finance-notice-discard').click();await tick();assert.equal(dialog.open,false);
  el('fin-new-notice').click();await tick();assert.equal(el('finance-notice-title').value,'');
});
test('ordinary users select one expense group and resubmit a rejected version without discarding its files',async t=>{
  const item=application({status:'rejected',review_note:'请补齐材料'});
  const {w,el,requests}=await setup(t,{mine:[item],groups:[{id:'a',name:'A'},{id:'b',name:'B'}]});
  assert.ok(el('finance-tabs').classList.contains('hidden'));
  assert.ok(el('finance-admin').classList.contains('hidden'));
  assert.ok(!requests.some(([url])=>url.includes('/admin/')));
  w.document.querySelector('[data-edit-finance]').click();
  assert.ok(el('finance-dialog').open);assert.equal(el('fin-expense-label').hidden,false);
  assert.equal(el('fin-expense-group').required,true);assert.equal(el('finance-invoice').required,false);
  assert.equal(el('finance-submit').textContent,'修改并重提');
  const fields={number:'20261122000000000001',code:'',date:'2026-11-22',total:'1000.00',check_code:''};
  w.FinanceInvoices={getFields:()=>fields};
  el('fin-expense-group').value='b';el('finance-project').value='新订阅';
  el('finance-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
  const [url,options]=requests.find(([,opts])=>opts.method==='PUT');
  assert.equal(url,'/api/finance/applications/app-1');
  assert.equal(options.body.get('expense_group_id'),'b');assert.equal(options.body.get('version'),'1');
  assert.equal(options.body.get('amount'),null);assert.deepEqual(JSON.parse(options.body.get('invoice_fields')),fields);
  assert.equal(options.body.get('invoice'),null);assert.equal(el('finance-dialog').open,false);
});
test('admin review defaults to all-time pending; own applications stay private and stats use expense attribution once',async t=>{
  const pending=application({created_at:'2020-01-01T00:00:00Z'}), approved=application({id:'app-2',status:'approved',amount:'20.00',amount_cents:2000}), cancelled=application({id:'app-3',status:'cancelled',amount:'99.00',amount_cents:9900});
  const {w,el}=await setup(t,{admin:true,all:[pending,approved,cancelled],mine:[]});
  assert.equal(el('fin-period').value,'all');assert.equal(el('fin-status').value,'pending');
  assert.ok(el('admin-finance-rows').textContent.includes('模型订阅'));
  w.document.querySelector('[data-fin-tab="mine"]').click();
  assert.ok(el('finance-rows').textContent.includes('没有匹配的申请'));assert.equal(el('finance-total').textContent,'0');
  w.document.querySelector('[data-fin-tab="stats"]').click();
  el('fin-period').value='all';el('fin-period').dispatchEvent(new w.Event('change'));
  assert.equal(el('fin-stat-cards').querySelector('.stat-value').textContent,'¥32.50');
  assert.ok(el('fin-trend').querySelector('svg title').textContent.includes('12.50'));
  el('fin-breakdown').value='group';el('fin-breakdown').dispatchEvent(new w.Event('change'));
  assert.equal(el('fin-donut').querySelectorAll('circle').length,1);
  assert.ok(el('fin-donut').textContent.includes('32.50'));
});
test('review rows open details from text or the keyboard while action buttons and download links keep their behavior',async t=>{
  const {w,el,requests}=await setup(t,{admin:true,all:[application()]});
  const original=w.fetch;
  w.fetch=async(url,options)=>url==='/api/finance/applications/app-1/history'
    ?(requests.push([url,options]),{ok:true,json:async()=>({item:application(),items:[{id:'event-1',version:1,action:'submitted',created_at:new Date().toISOString(),actor_name:'alice',snapshot:application()}]})})
    :original(url,options);
  const row=el('admin-finance-rows').querySelector('tr');
  row.querySelector('.fin-project').click();await tick();
  assert.equal(el('fin-detail-dialog').open,true);
  assert.match(el('fin-detail-content').textContent,/模型订阅/);
  assert.match(el('fin-detail-content').querySelector('.fin-detail-summary a').href,/event_id=event-1/);
  el('fin-detail-dialog').close();
  const count=()=>requests.filter(([url])=>url.endsWith('/history')&&!url.includes('/admin/')).length;
  const before=count();row.querySelector('[data-review]').click();await tick();
  assert.equal(count(),before);assert.equal(el('fin-review-dialog').open,true);el('fin-review-dialog').close();
  row.querySelector('a').addEventListener('click',event=>event.preventDefault());row.querySelector('a').click();await tick();
  assert.equal(count(),before);
  row.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));await tick();
  assert.equal(count(),before+1);assert.equal(el('fin-detail-dialog').open,true);
});
test('late application details cannot replace a newer application or reopen a closed detail dialog',async t=>{
  const {w,el}=await setup(t,{admin:true,all:[application(),application({id:'app-2',project:'新申请'})]});
  const pending=new Map(),original=w.fetch;
  w.fetch=(url,options)=>url.includes('/applications/')&&url.endsWith('/history')?new Promise(resolve=>pending.set(url,resolve)):original(url,options);
  el('admin-finance-rows').querySelector('[data-open-finance="app-1"]').click();await tick();
  el('admin-finance-rows').querySelector('[data-open-finance="app-2"]').click();await tick();
  pending.get('/api/finance/applications/app-2/history')({ok:true,json:async()=>({item:application({id:'app-2',project:'新申请'}),items:[]})});await tick();
  pending.get('/api/finance/applications/app-1/history')({ok:true,json:async()=>({item:application(),items:[]})});await tick();
  assert.match(el('fin-detail-content').textContent,/新申请/);
  assert.doesNotMatch(el('fin-detail-content').textContent,/模型订阅/);
  el('admin-finance-rows').querySelector('[data-open-finance="app-1"]').click();await tick();el('fin-detail-dialog').close();
  pending.get('/api/finance/applications/app-1/history')({ok:true,json:async()=>({item:application(),items:[]})});await tick();
  assert.equal(el('fin-detail-dialog').open,false);assert.equal(el('fin-detail-content').textContent,'');
});
test('detail footer reviews use the displayed version and refresh to re-review with the latest decision',async t=>{
  const {w,el,requests}=await setup(t,{admin:true,all:[application()]});
  let current=application({version:2}),events=[{id:'submitted-2',version:2,action:'submitted',created_at:new Date().toISOString(),actor_name:'alice',snapshot:application({version:2})}],reviews=0;
  const original=w.fetch;
  w.fetch=async(url,options={})=>{
    if(url==='/api/finance/applications/app-1/history'){
      requests.push([url,options]);return {ok:true,json:async()=>({item:current,items:events})};
    }
    if(url==='/api/admin/finance/applications/app-1/review'||url==='/api/admin/finance/applications/app-1/re-review'){
      requests.push([url,options]);const payload=JSON.parse(options.body);reviews++;current={...current,status:payload.status};
      events=events.map(event=>({...event,is_current_review:false}));
      events.unshift({id:'review-'+reviews,version:2,action:payload.status,is_current_review:true,created_at:new Date().toISOString(),actor_name:'admin',snapshot:current});
      return {ok:true,json:async()=>({item:current})};
    }
    if(url==='/api/admin/finance/applications')return {ok:true,json:async()=>({items:[current]})};
    return original(url,options);
  };
  el('admin-finance-rows').querySelector('.fin-project').click();await tick();
  assert.equal(el('fin-detail-actions').hidden,false);
  el('fin-detail-actions').querySelector('[data-detail-review="rejected"]').click();assert.equal(el('fin-review-note').required,true);el('fin-review-dialog').close();
  el('fin-detail-actions').querySelector('[data-detail-review="approved"]').click();
  el('fin-review-form').dispatchEvent(new w.Event('submit',{cancelable:true}));for(let i=0;i<4;i++)await tick();
  let payload=JSON.parse(requests.find(([url])=>url.endsWith('/review'))[1].body);assert.equal(payload.version,2);assert.equal(payload.status,'approved');
  assert.equal(el('fin-detail-actions').querySelector('[data-detail-review]'),null);
  assert.equal(el('fin-detail-actions').querySelector('[data-detail-re-review]').dataset.detailReReview,'review-1');
  el('fin-detail-actions').querySelector('button').click();el('fin-re-review-result').value='rejected';
  el('fin-review-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();assert.equal(reviews,1);assert.match(el('fin-review-error').textContent,/改审原因/);
  el('fin-review-note').value='修正误操作';el('fin-review-form').dispatchEvent(new w.Event('submit',{cancelable:true}));for(let i=0;i<4;i++)await tick();
  payload=JSON.parse(requests.find(([url])=>url.endsWith('/re-review'))[1].body);assert.deepEqual(payload,{status:'rejected',note:'修正误操作',version:2,event_id:'review-1'});
  assert.equal(el('fin-detail-actions').querySelector('[data-detail-re-review]').dataset.detailReReview,'review-2');
  assert.match(el('fin-detail-content').textContent,/已驳回/);
});
test('review footer stays hidden for ordinary users, cancelled applications and announcement details',async t=>{
  const user=await setup(t,{mine:[application()]});user.el('finance-rows').querySelector('[data-history]').click();await tick();
  assert.equal(user.el('fin-detail-actions').hidden,true);assert.equal(user.el('fin-detail-actions').children.length,0);
  assert.ok(!user.requests.some(([url])=>url.includes('/admin/')));
  const admin=await setup(t,{admin:true,all:[application({status:'cancelled'})]});
  admin.el('fin-status').value='all';admin.el('fin-status').dispatchEvent(new admin.w.Event('change'));admin.el('admin-finance-rows').querySelector('.fin-project').click();await tick();
  assert.equal(admin.el('fin-detail-actions').hidden,true);
  admin.el('fin-detail-dialog').close();admin.w.document.querySelector('[data-read-notice]').click();
  assert.equal(admin.el('fin-detail-actions').hidden,true);
  assert.equal(admin.el('fin-detail-dialog').classList.contains('fin-application-detail'),false);
});
test('history selects only approved records and review requires a reason with the displayed version',async t=>{
  const item=application(), snapshot=application({status:'pending'});
  const history=[{id:'good',is_current_review:true,action:'approved',actor_id:'admin',actor_name:'admin',version:1,created_at:new Date().toISOString(),note:'',snapshot},{id:'bad',action:'rejected',actor_id:'admin',actor_name:'admin',version:1,created_at:new Date().toISOString(),note:'原因',snapshot}];
  const {w,el,requests}=await setup(t,{admin:true,all:[item],history});
  w.document.querySelector('[data-review][data-result="rejected"]').click();
  assert.ok(el('fin-review-dialog').open);assert.equal(el('fin-review-note').required,true);
  el('fin-review-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.ok(el('fin-review-error').textContent.includes('驳回原因'));assert.ok(!requests.some(([,opts])=>opts.method==='POST'));
  el('fin-review-note').value='请补发票';el('fin-review-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.deepEqual(JSON.parse(requests.find(([,opts])=>opts.method==='POST')[1].body),{status:'rejected',note:'请补发票',version:1});
  w.document.querySelector('[data-fin-tab="history"]').click();
  assert.equal(el('fin-history-rows').querySelectorAll('input[type="checkbox"]').length,1);
  assert.ok(el('fin-history-rows').querySelector('a').href.includes('event_id=good'));
  el('fin-select-all').checked=true;el('fin-select-all').dispatchEvent(new w.Event('change'));assert.equal(el('finance-batch-download').disabled,false);
  let downloadName='';
  w.URL.createObjectURL=()=> 'blob:finance-fixture';w.URL.revokeObjectURL=()=>{};
  w.HTMLAnchorElement.prototype.click=function(){downloadName=this.download;};
  const originalFetch=w.fetch;
  w.fetch=async(url,options)=> url==='/api/admin/finance/download.zip'
    ? (requests.push([url,options]), {ok:true,blob:async()=>new w.Blob(['zip'])}) : originalFetch(url,options);
  el('finance-batch-download').click();await tick();
  assert.deepEqual(JSON.parse(requests.at(-1)[1].body),{event_ids:['good']});
  assert.equal(downloadName,'approved-finance.zip');
  el('fin-search').value='不存在';el('fin-search').dispatchEvent(new w.Event('input'));assert.equal(el('finance-batch-download').disabled,true);
});
test('announcements open in dialogs and user-provided titles and filenames render as text',async t=>{
  const {w,el}=await setup(t,{mine:[application({files:[{kind:'invoice',original_name:'<img src=x onerror=alert(1)>.pdf'}]})]});
  w.document.querySelector('[data-read-notice]').click();assert.ok(el('fin-detail-dialog').open);
  assert.ok(el('fin-detail-content').textContent.includes('公告<1>'));assert.equal(el('fin-detail-content').querySelector('img'),null);
  assert.equal(el('finance-rows').querySelector('img'),null);
  w.document.querySelector('[data-close-dialog="fin-detail-dialog"]').click();assert.equal(el('fin-detail-dialog').open,false);
  el('fin-notice-history').click();assert.ok(el('fin-notice-history-dialog').open);assert.ok(el('fin-notice-history-list').querySelector('[data-read-notice]'));
});

test('only the first three finance notices appear at the top while all remain readable in history and management',async t=>{
  const announcements=Array.from({length:5},(_,i)=>({id:'notice-'+i,title:'公告'+i,body:'内容'+i,pinned:i<2}));
  const {w,el}=await setup(t,{admin:true,announcements});
  const visible=[...el('announcements').querySelectorAll('[data-read-notice]')];
  assert.deepEqual(visible.map(button=>button.dataset.readNotice),['notice-0','notice-1','notice-2']);
  assert.equal(el('admin-announcements').querySelectorAll('[data-read-notice]').length,5);
  visible[2].click();assert.ok(el('fin-detail-content').textContent.includes('内容2'));
  w.document.querySelector('[data-close-dialog="fin-detail-dialog"]').click();el('fin-notice-history').click();
  const history=el('fin-notice-history-list').querySelectorAll('[data-read-notice]');
  assert.equal(history.length,5);history[4].click();
  assert.ok(el('fin-detail-content').textContent.includes('内容4'));
});

test('finance notices do not add empty rows when fewer than three are published',async t=>{
  for(const count of [0,1,3]){
    const announcements=Array.from({length:count},(_,i)=>({id:'notice-'+i,title:'公告'+i,body:'内容',pinned:false}));
    const {el}=await setup(t,{announcements});
    assert.equal(el('announcements').querySelectorAll('[data-read-notice]').length,count);
    assert.ok(el('fin-notice-history'));
    if(!count){assert.match(el('announcements').textContent,/暂无公告/);el('fin-notice-history').click();assert.match(el('fin-notice-history-list').textContent,/暂无公告/);assert.equal(el('fin-notice-prev').disabled,true);assert.equal(el('fin-notice-next').disabled,true);}
  }
});

test('past announcements paginate all records and retain the selected page after reading a notice',async t=>{
  const announcements=Array.from({length:26},(_,i)=>({id:'notice-'+i,title:'公告'+i,body:'内容'+i,pinned:i<2}));
  const {w,el,requests}=await setup(t,{announcements});el('fin-notice-history').click();
  const buttons=()=>[...el('fin-notice-history-list').querySelectorAll('[data-read-notice]')];
  assert.equal(buttons().length,10);assert.equal(buttons()[0].dataset.readNotice,'notice-0');
  assert.equal(el('fin-notice-history-count').textContent,'26 条公告');assert.equal(el('fin-notice-history-page').textContent,'1 / 3');
  assert.equal(el('fin-notice-prev').disabled,true);el('fin-notice-next').click();
  assert.equal(buttons()[0].dataset.readNotice,'notice-10');el('fin-notice-next').click();
  assert.equal(buttons().length,6);assert.equal(el('fin-notice-next').disabled,true);assert.equal(el('fin-notice-history-page').textContent,'3 / 3');
  el('fin-notice-prev').click();buttons()[0].click();
  assert.ok(el('fin-detail-dialog').open);assert.ok(el('fin-detail-content').textContent.includes('内容10'));
  el('fin-detail-content').querySelector('[data-close-dialog="fin-detail-dialog"]').click();
  assert.equal(el('fin-detail-dialog').open,false);assert.ok(el('fin-notice-history-dialog').open);
  assert.equal(el('fin-notice-history-page').textContent,'2 / 3');assert.equal(buttons()[0].dataset.readNotice,'notice-10');
  announcements.splice(8);await w.financeFixtureController.load();
  assert.equal(el('fin-notice-history-page').textContent,'1 / 1');assert.equal(buttons().length,8);
  assert.ok(!requests.some(([url])=>url.includes('/admin/')));
});

test('past notice search matches titles and content, resets pagination, and preserves the query on return',async t=>{
  const announcements=Array.from({length:26},(_,i)=>({id:'notice-'+i,title:i===1?'研讨会补贴':'公告'+i,body:i%2?'住宿说明':'Travel PAYMENT <img src=x onerror=alert(1)>',pinned:false}));
  const {w,el}=await setup(t,{announcements});el('fin-notice-history').click();
  const search=value=>{el('fin-notice-search').value=value;el('fin-notice-search').dispatchEvent(new w.Event('input'));};
  search('  travel  ');assert.equal(el('fin-notice-history-count').textContent,'13 条公告');
  assert.equal(el('fin-notice-history-list').querySelector('img'),null);el('fin-notice-next').click();
  assert.equal(el('fin-notice-history-list').querySelectorAll('[data-read-notice]').length,3);
  el('fin-notice-history-list').querySelector('[data-read-notice]').click();
  assert.equal(el('fin-detail-content').querySelector('img'),null);
  el('fin-detail-content').querySelector('[data-close-dialog="fin-detail-dialog"]').click();
  assert.equal(el('fin-notice-search').value,'  travel  ');assert.equal(el('fin-notice-history-page').textContent,'2 / 2');
  search('研讨会');assert.equal(el('fin-notice-history-count').textContent,'1 条公告');
  assert.equal(el('fin-notice-history-list').querySelector('[data-read-notice]').dataset.readNotice,'notice-1');
  assert.equal(el('fin-notice-history-page').textContent,'1 / 1');assert.equal(el('fin-notice-prev').disabled,true);assert.equal(el('fin-notice-next').disabled,true);
  search('没有这个词');assert.match(el('fin-notice-history-list').textContent,/没有匹配的公告/);
  assert.equal(el('fin-notice-history-count').textContent,'0 条公告');
  search('');assert.equal(el('fin-notice-history-count').textContent,'26 条公告');assert.equal(el('fin-notice-history-page').textContent,'1 / 3');
  w.document.querySelector('[data-close-dialog="fin-notice-history-dialog"]').click();
  assert.equal(el('fin-notice-history-dialog').open,false);
  el('fin-notice-history').click();assert.equal(el('fin-notice-search').value,'');
  el('fin-notice-history-list').querySelector('[data-read-notice]').click();
  w.history.replaceState(null,'','#view-api2api');w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  assert.equal(el('fin-detail-dialog').open,false);assert.equal(el('fin-notice-history-dialog').open,false);
});

test('announcement management searches and paginates independently of past announcements and application filters',async t=>{
  const announcements=Array.from({length:26},(_,i)=>({id:'notice-'+i,title:i===25?'研讨会补贴':'公告'+i,body:i<13?'Travel PAYMENT <img src=x onerror=alert(1)>':'其他正文',pinned:i<2}));
  const {w,el}=await setup(t,{admin:true,announcements});w.document.querySelector('[data-fin-tab="notices"]').click();
  const rows=()=>[...el('admin-announcements').querySelectorAll('[data-read-notice]')];
  const search=value=>{el('fin-admin-notice-search').value=value;el('fin-admin-notice-search').dispatchEvent(new w.Event('input'));};
  assert.equal(el('fin-search').closest('label').hidden,true);assert.equal(rows().length,10);
  assert.equal(el('fin-admin-notice-count').textContent,'26 条公告');assert.equal(el('fin-admin-notice-prev').disabled,true);
  el('fin-admin-notice-next').click();assert.equal(rows()[0].dataset.readNotice,'notice-10');
  el('fin-admin-notice-next').click();assert.equal(rows().length,6);assert.equal(el('fin-admin-notice-next').disabled,true);
  el('fin-notice-history').click();assert.equal(el('fin-notice-history-page').textContent,'1 / 3');
  el('fin-notice-next').click();assert.equal(el('fin-admin-notice-page').textContent,'3 / 3');
  w.document.querySelector('[data-close-dialog="fin-notice-history-dialog"]').click();
  search('  tRAvEL  ');assert.equal(el('fin-admin-notice-count').textContent,'13 条公告');assert.equal(el('fin-admin-notice-page').textContent,'1 / 2');
  assert.equal(el('admin-announcements').querySelector('img'),null);el('fin-admin-notice-next').click();assert.equal(rows().length,3);
  rows()[0].click();assert.ok(el('fin-detail-dialog').open);assert.equal(el('fin-detail-content').querySelector('img'),null);
  w.document.querySelector('[data-close-dialog="fin-detail-dialog"]').click();assert.equal(el('fin-admin-notice-page').textContent,'2 / 2');
  search('研讨会');assert.equal(rows()[0].dataset.readNotice,'notice-25');assert.equal(el('fin-admin-notice-count').textContent,'1 条公告');
  search('没有这个词');assert.match(el('admin-announcements').textContent,/没有匹配的公告/);assert.equal(el('fin-admin-notice-next').disabled,true);
  assert.equal(el('announcements').querySelectorAll('[data-read-notice]').length,3);
  search('');assert.equal(el('fin-admin-notice-count').textContent,'26 条公告');
  w.document.querySelector('[data-fin-tab="review"]').click();assert.equal(el('fin-search').closest('label').hidden,false);
});

test('editing and pinning retain management search and page while deleting the final item clamps pagination',async t=>{
  const announcements=Array.from({length:12},(_,i)=>({id:'notice-'+i,title:'公告'+i,body:i<11?'Travel materials':'Other materials',pinned:false}));
  const {w,el,requests}=await setup(t,{admin:true,richEditor:true,announcements});
  const original=w.fetch;w.fetch=async(url,options={})=>{
    if(url.startsWith('/api/admin/finance/announcements/')&&['PUT','DELETE'].includes(options.method)){
      requests.push([url,options]);const index=announcements.findIndex(item=>item.id===url.split('/').at(-1));
      if(options.method==='DELETE')announcements.splice(index,1);else Object.assign(announcements[index],JSON.parse(options.body));
      return {ok:true,json:async()=>({})};
    }
    return original(url,options);
  };
  w.document.querySelector('[data-fin-tab="notices"]').click();el('fin-admin-notice-search').value='travel';el('fin-admin-notice-search').dispatchEvent(new w.Event('input'));el('fin-admin-notice-next').click();
  el('admin-announcements').querySelector('[data-edit-notice]').click();await tick();assert.equal(el('finance-notice-title').value,'公告10');
  el('finance-notice-title').value='已修改公告';el('finance-notice-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();await tick();
  assert.equal(el('finance-notice-edit-dialog').open,false);assert.equal(el('fin-admin-notice-page').textContent,'2 / 2');assert.equal(el('fin-admin-notice-search').value,'travel');
  assert.equal(el('admin-announcements').querySelector('[data-read-notice]').textContent,'已修改公告');
  el('admin-announcements').querySelector('[data-pin-notice]').click();await tick();await tick();
  assert.equal(announcements[10].pinned,true);assert.equal(el('fin-admin-notice-page').textContent,'2 / 2');
  assert.deepEqual(JSON.parse(requests.filter(([url,opts])=>url.endsWith('/notice-10')&&opts.method==='PUT').at(-1)[1].body).blocks,announcements[10].blocks);
  assert.equal(el('admin-announcements').querySelector('[data-pin-notice]').textContent,'取消置顶');
  w.confirm=()=>false;el('admin-announcements').querySelector('[data-delete-notice]').click();await tick();
  assert.ok(!requests.some(([,opts])=>opts.method==='DELETE'));
  w.confirm=()=>true;el('admin-announcements').querySelector('[data-delete-notice]').click();await tick();await tick();
  assert.equal(el('fin-admin-notice-search').value,'travel');assert.equal(el('fin-admin-notice-count').textContent,'10 条公告');
  assert.equal(el('fin-admin-notice-page').textContent,'1 / 1');assert.equal(el('fin-admin-notice-prev').disabled,true);assert.equal(el('fin-admin-notice-next').disabled,true);
  assert.equal(requests.find(([,opts])=>opts.method==='DELETE')[0],'/api/admin/finance/announcements/notice-10');
});

test('report export uses all current filters, own scope and the active report type',async t=>{
  const {w,el,requests}=await setup(t,{admin:true,mine:Array.from({length:30},(_,i)=>application({id:'app-'+i}))});
  w.URL.createObjectURL=()=> 'blob:finance-report';w.URL.revokeObjectURL=()=>{};let filename='';w.HTMLAnchorElement.prototype.click=function(){filename=this.download;};
  const original=w.fetch;w.fetch=async(url,options)=>url==='/api/finance/reports/export'?(requests.push([url,options]),{ok:true,blob:async()=>new w.Blob(['report'])}):original(url,options);
  w.document.querySelector('[data-fin-tab="mine"]').click();el('fin-search').value='订阅';
  w.document.querySelector('[data-fin-export="xlsx"]').click();await tick();
  let payload=JSON.parse(requests.at(-1)[1].body);assert.equal(payload.scope,'mine');assert.equal(payload.filters.q,'订阅');assert.equal(payload.report,'applications');assert.equal(filename,'finance-applications.xlsx');
  w.document.querySelector('[data-fin-tab="history"]').click();w.document.querySelector('[data-fin-export="csv"]').click();await tick();
  payload=JSON.parse(requests.at(-1)[1].body);assert.equal(payload.scope,'all');assert.equal(payload.report,'history');assert.equal(payload.format,'csv');
  w.document.querySelector('[data-fin-tab="stats"]').click();w.document.querySelector('[data-fin-export="xlsx"]').click();await tick();
  assert.equal(JSON.parse(requests.at(-1)[1].body).report,'groups');
  w.document.querySelector('[data-fin-tab="notices"]').click();assert.equal(el('fin-export-menu').hidden,true);
});

test('finance dialogs dismiss on a backdrop click but keep edits open for interior clicks or drags',async t=>{
  const {w,el}=await setup(t);
  el('finance-open-form').click();
  const dialog=el('finance-dialog');
  dialog.getBoundingClientRect=()=>({left:100,top:100,right:660,bottom:650});
  const pointer=(type,x,y)=>dialog.dispatchEvent(new w.MouseEvent(type,{clientX:x,clientY:y,bubbles:true}));
  el('finance-project').value='未提交草稿';
  pointer('pointerdown',120,120);pointer('click',120,120);
  assert.equal(dialog.open,true);
  pointer('pointerdown',120,120);pointer('click',20,20);
  assert.equal(dialog.open,true);
  pointer('pointerdown',20,20);pointer('click',20,20);
  assert.equal(dialog.open,false);
  assert.equal(el('finance-project').value,'未提交草稿');
});

test('compact finance filters survive tab moves, period buttons filter real data, and chart details support the keyboard',async t=>{
  const today=application(), old=application({id:'old',created_at:'2020-01-01T00:00:00Z'});
  const {w,el}=await setup(t,{admin:true,all:[today,old]});
  const filters=el('finance-filter-card'), search=el('fin-search');
  assert.ok(el('fin-panel-review').contains(filters));
  assert.ok(!filters.contains(search));
  w.document.querySelector('[data-fin-tab="stats"]').click();
  assert.equal(el('finance-filter-card'),filters);
  assert.ok(el('fin-panel-stats').contains(filters));
  w.document.querySelector('[data-fin-period="day"]').click();
  assert.equal(el('fin-period').value,'day');
  assert.equal(el('fin-stat-cards').querySelector('.stat-value').textContent,'¥12.50');
  w.document.querySelector('[data-fin-chart="trend"]').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  assert.equal(el('fin-trend-details').open,true);
  w.document.querySelector('[data-fin-tab="review"]').click();
  assert.ok(el('fin-panel-review').contains(filters));
  el('fin-status').value='all';el('fin-status').dispatchEvent(new w.Event('change'));
  search.value='old';search.dispatchEvent(new w.Event('input'));
  assert.equal(el('admin-finance-rows').querySelectorAll('tr').length,1);
  assert.ok(el('admin-finance-rows').textContent.includes('old'));
  assert.equal(el('admin-finance-rows').querySelector('.fin-file').getAttribute('title'),'Invoice.pdf');
});

test('re-review is offered only for the current decision and submits its event id with a mandatory reason',async t=>{
  for(const current of ['approved','rejected']){
    const item=application({status:current}),next=current==='approved'?'rejected':'approved';
    const history=[{id:'latest',application_id:item.id,action:current,actor_id:'admin',actor_name:'admin',version:1,created_at:new Date().toISOString(),note:'原审批',snapshot:application(),is_current_review:true},{id:'old',application_id:item.id,action:next,actor_id:'admin',actor_name:'admin',version:1,created_at:new Date().toISOString(),note:'更早审批',snapshot:application(),is_current_review:false}];
    const {w,el,requests}=await setup(t,{admin:true,all:[item],history});
    w.document.querySelector('[data-fin-tab="history"]').click();
    assert.equal(el('fin-history-rows').querySelectorAll('[data-re-review]').length,1);
    assert.equal(el('fin-history-rows').querySelectorAll('[data-select-event]').length,current==='approved'?1:0);
    assert.match(el('fin-history-rows').textContent,/已被后续操作替代/);
    assert.ok(el('fin-filter-summary').textContent.includes('当前有效通过 '+(current==='approved'?'¥12.50':'¥0.00')));
    el('fin-history-rows').querySelector('[data-re-review]').click();
    assert.equal(el('fin-review-title').textContent,'再审核申请');
    assert.equal(el('fin-review-note').required,true);
    assert.equal(el('fin-re-review-result').querySelector(`[value="${current}"]`).disabled,true);
    el('fin-re-review-result').value=next;
    el('fin-review-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
    assert.match(el('fin-review-error').textContent,/改审原因/);
    assert.ok(!requests.some(([,opts])=>opts.method==='POST'));
    el('fin-review-note').value='纠正误操作';
    el('fin-review-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
    const [url,options]=requests.find(([,opts])=>opts.method==='POST');
    assert.equal(url,'/api/admin/finance/applications/app-1/re-review');
    assert.deepEqual(JSON.parse(options.body),{status:next,note:'纠正误操作',version:1,event_id:'latest'});
  }
});
