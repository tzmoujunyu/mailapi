const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const assets = path.resolve(__dirname, '../../mailapi_shared/static');
const read = name => fs.readFileSync(path.join(assets,name),'utf8');
const tick = () => new Promise(resolve=>setImmediate(resolve));
const application = (extra={}) => ({id:'app-1', username:'alice', user_note:'Agent', project:'模型订阅', amount:'12.50', amount_cents:1250, version:1, groups:['A','B'], expense_group_id:'a', expense_group_name:'A', status:'pending', created_at:new Date().toISOString(), files:[{kind:'invoice',original_name:'Invoice.pdf'}], ...extra});
async function setup(t, {admin=false, mine=[], all=[], history=[], groups=[]}={}) {
  const dom=new JSDOM(read('test-features.html'),{url:'http://localhost/test-features#view-finance',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.confirm=()=>true;
  const requests=[];
  const fixtures={'/api/finance/applications':{items:mine},'/api/finance/announcements':{items:[{id:'notice',title:'公告<1>',body:'请补齐材料',pinned:true}]},'/api/finance/groups':{items:groups},'/api/admin/finance/applications':{items:all},'/api/admin/finance/history':{items:history}};
  w.fetch=async(url,options={})=>{requests.push([url,options]);return {ok:true,json:async()=>fixtures[url] || {items:[]}};};
  w.eval(read('test-finance.js')+`\nwindow.financeFixtureController=financeController;`);
  w.financeFixtureController.start(admin);await w.financeFixtureController.load();
  return {w,el:id=>w.document.getElementById(id),requests};
}
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
  el('fin-expense-group').value='b';el('finance-project').value='新订阅';
  el('finance-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
  const [url,options]=requests.find(([,opts])=>opts.method==='PUT');
  assert.equal(url,'/api/finance/applications/app-1');
  assert.equal(options.body.get('expense_group_id'),'b');assert.equal(options.body.get('version'),'1');
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
  el('fin-notice-history').click();assert.ok(el('fin-detail-content').querySelector('[data-read-notice]'));
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
