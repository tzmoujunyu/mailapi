const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const assets=path.resolve(__dirname,'../../mailapi_shared/static');
const read=name=>fs.readFileSync(path.join(assets,name),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function setup(t,{drafts=new Map(),active='',admin=false}={}){
  const dom=new JSDOM(read('test-features.html'),{url:'http://localhost/test-features#view-finance',runScripts:'outside-only'}),w=dom.window;
  t.after(()=>w.close());
  const el=id=>w.document.getElementById(id),requests=[];
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){if(this.open){this.open=false;this.dispatchEvent(new w.Event('close'));}};
  if(active)w.sessionStorage.setItem('finance.active-draft',active);
  const originalTimeout=w.setTimeout.bind(w);w.setTimeout=(fn,ms)=>originalTimeout(fn,ms===700?15:ms);
  const publicItem=item=>({...item,files:Object.entries(item.blobs).map(([kind,file])=>({kind,original_name:file.name,content_type:file.type,size:file.size}))});
  let fail=false,hold=null;
  w.fetch=async(url,options={})=>{
    requests.push([url,options]);const method=options.method||'GET';
    if(url==='/api/finance/drafts')return {ok:true,json:async()=>({items:[...drafts.values()].map(publicItem)})};
    if(url.startsWith('/api/finance/drafts/')){
      const id=url.split('/')[4].split('?')[0];
      if(method==='PUT'){
        if(hold)await hold;
        if(fail)return {ok:false,status:503,json:async()=>({detail:'保存失败'})};
        const before=drafts.get(id),revision=Number(options.body.get('revision'));
        if(revision!==(before?.revision||0))return {ok:false,status:409,json:async()=>({detail:'草稿已在其他页面更新，请关闭后重新打开草稿'})};
        const blobs={...before?.blobs};for(const [kind,key] of [['invoice','invoice'],['payment','payment_record']])if(options.body.has(key))blobs[kind]=options.body.get(key);
        const item={id,revision:revision+1,fields:JSON.parse(options.body.get('fields')),blobs,updated_at:new Date().toISOString()};drafts.set(id,item);
        return {ok:true,json:async()=>({item:publicItem(item)})};
      }
      if(url.includes('/files/'))return {ok:true,blob:async()=>new w.Blob(['restored attachment'],{type:'application/pdf'})};
      if(method==='DELETE'){drafts.delete(id);return {ok:true,json:async()=>({ok:true})};}
      return {ok:true,json:async()=>({item:publicItem(drafts.get(id))})};
    }
    if(url==='/api/finance/groups')return {ok:true,json:async()=>({items:[{id:'g1',name:'组一'},{id:'g2',name:'组二'}]})};
    if(url==='/api/admin/finance/payers')return {ok:true,json:async()=>({items:[{id:'alice',username:'alice',groups:[{id:'g1',name:'组一'}]}]})};
    if(method==='POST'&&url.endsWith('/finance/applications')){
      if(fail)return {ok:false,status:409,json:async()=>({detail:'发票已占用'})};
      drafts.delete(options.body.get('draft_id'));return {ok:true,json:async()=>({item:{id:'submitted'}})};
    }
    return {ok:true,json:async()=>({items:[]})};
  };
  w.eval(read('test-finance-invoices.js'));w.eval(read('test-finance-drafts.js'));
  w.eval(read('test-finance.js')+'\nwindow.fc=financeController;');
  w.fc.start(admin,'admin');await w.fc.load();await tick();await tick();
  const input=(id,value)=>{el(id).value=value;el(id).dispatchEvent(new w.Event('input',{bubbles:true}));};
  const open=()=>{el('finance-open-form').click();if(admin)el('fin-apply-self').click();};
  const upload=(id,name)=>{const value=new w.File(['attachment'],name,{type:'application/pdf'});Object.defineProperty(el(id),'files',{configurable:true,value:[value]});el(id).dispatchEvent(new w.Event('change',{bubbles:true}));return value;};
  return {w,el,requests,drafts,input,open,upload,fail:value=>fail=value,hold:value=>hold=value};
}
const autosave=()=>new Promise(resolve=>setTimeout(resolve,50));
test('autosaves partial text and both files, subsequent typing does not reupload attachments',async t=>{
  const f=await setup(t);f.open();f.input('finance-project','未填完的项目');f.input('finance-note','私密备注');
  f.upload('finance-payment','支付记录.pdf');await autosave();
  assert.equal(f.drafts.size,1);const draft=[...f.drafts.values()][0];assert.equal(draft.fields.application_note,'私密备注');assert.ok(draft.blobs.payment);
  f.input('finance-note','第二次修改');await autosave();
  const puts=f.requests.filter(([,o])=>o.method==='PUT');assert.equal(puts.length,2);assert.equal(puts[1][1].body.has('payment_record'),false);
  assert.equal(f.el('fin-draft-status').textContent,'草稿已保存');
  assert.equal(f.el('finance-pending').textContent,'0');
});
test('refresh restores server text and attachments, requires invoice confirmation and submits the saved revision',async t=>{
  const f=await setup(t);f.open();f.input('finance-project','恢复演示');f.input('finance-note','恢复备注');f.input('fin-expense-group','g2');
  f.upload('finance-payment','支付凭据.pdf');
  // Keep OCR out of this draft-specific test; it has separate integration coverage.
  Object.defineProperty(f.el('finance-invoice'),'files',{value:[new f.w.File(['invoice'],'发票.pdf',{type:'application/pdf'})]});
  f.input('fin-inv-number','20261124000000000001');f.input('fin-inv-date','2026-11-24');f.input('fin-inv-total','1000.00');await autosave();
  const id=[...f.drafts.keys()][0];const restored=await setup(t,{drafts:f.drafts,active:id});
  assert.equal(restored.el('finance-dialog').open,true);assert.equal(restored.el('finance-note').value,'恢复备注');
  assert.equal(restored.el('fin-expense-group').value,'g2');assert.equal(restored.el('fin-payment-name').textContent,'支付凭据.pdf');
  assert.equal(restored.el('fin-invoice-name').textContent,'发票.pdf');assert.equal(restored.el('finance-invoice').required,false);
  assert.equal(restored.el('fin-inv-confirm').checked,false);restored.el('fin-inv-confirm').checked=true;
  restored.el('finance-form').dispatchEvent(new restored.w.Event('submit',{cancelable:true}));await tick();await tick();
  const submission=restored.requests.find(([url,o])=>url==='/api/finance/applications'&&o.method==='POST')[1].body;
  assert.equal(submission.get('draft_id'),id);assert.equal(submission.get('draft_revision'),'1');
  assert.equal(submission.get('invoice').name,'发票.pdf');assert.equal(submission.get('payment_record').name,'支付凭据.pdf');
  assert.equal(restored.drafts.size,0);assert.equal(restored.el('finance-dialog').open,false);assert.equal(restored.w.sessionStorage.getItem('finance.active-draft'),null);
});
test('unsaved cancel and Escape offer stay, save-and-close, and discard; saved drafts close without interruption',async t=>{
  const f=await setup(t);f.open();f.input('finance-project','尚未保存');
  f.el('finance-dialog').dispatchEvent(new f.w.Event('cancel',{cancelable:true}));await tick();
  assert.equal(f.el('fin-draft-leave-dialog').open,true);f.el('fin-draft-stay').click();await tick();assert.equal(f.el('finance-dialog').open,true);
  const close=f.w.FinanceDrafts.close();await tick();f.el('fin-draft-discard-changes').click();await close;assert.equal(f.el('finance-dialog').open,false);
  f.open();f.input('finance-project','保存后关闭');await autosave();await f.w.FinanceDrafts.close();assert.equal(f.el('fin-draft-leave-dialog').open,false);
});
test('failed autosave guards refresh and closing; retry saves before closing without losing fields',async t=>{
  const f=await setup(t);f.open();f.fail(true);f.input('finance-note','断网时保留');await autosave();
  assert.match(f.el('fin-draft-status').textContent,/保存失败/);
  const leaving=new f.w.Event('beforeunload',{cancelable:true});f.w.dispatchEvent(leaving);assert.equal(leaving.defaultPrevented,true);
  const close=f.w.FinanceDrafts.close();await tick();f.el('fin-draft-save-close').click();await tick();assert.equal(f.el('finance-dialog').open,true);
  assert.match(f.el('fin-draft-leave-status').textContent,/保存未完成/);
  f.fail(false);f.el('fin-draft-save-close').click();await close;
  assert.equal(f.el('finance-dialog').open,false);assert.equal([...f.drafts.values()][0].fields.application_note,'断网时保留');
});
test('typing during an in-flight save is not falsely marked saved and save-and-close flushes the latest text',async t=>{
  const f=await setup(t);let release;f.hold(new Promise(resolve=>release=resolve));f.open();f.input('finance-project','第一版');await autosave();
  f.input('finance-project','第二版');const close=f.w.FinanceDrafts.close();await tick();f.el('fin-draft-save-close').click();release();f.hold(null);await close;
  assert.equal([...f.drafts.values()][0].fields.project,'第二版');assert.equal(f.el('finance-dialog').open,false);
});
test('stale tab conflicts cannot overwrite the server draft and a different account never restores a leftover id',async t=>{
  const f=await setup(t);f.open();f.input('finance-project','初稿');await autosave();const id=[...f.drafts.keys()][0];
  f.drafts.get(id).revision++;f.drafts.get(id).fields.project='其他设备更新';f.input('finance-project','过期页面的编辑');await autosave();
  assert.equal(f.drafts.get(id).fields.project,'其他设备更新');assert.match(f.el('fin-draft-status').textContent,/其他页面/);
  const other=await setup(t,{active:id});assert.equal(other.el('finance-dialog').open,false);assert.equal(other.drafts.size,0);
});
test('administrator delegated draft restores payer and group rather than falling back to self',async t=>{
  const f=await setup(t,{admin:true});f.el('finance-open-form').click();f.el('fin-apply-other').click();await tick();
  f.el('fin-payer-user').value='alice';f.el('fin-payer-user').dispatchEvent(new f.w.Event('change',{bubbles:true}));f.input('finance-project','代提交草稿');await autosave();
  const id=[...f.drafts.keys()][0],again=await setup(t,{admin:true,drafts:f.drafts,active:id});
  assert.equal(again.el('fin-payer-user').value,'alice');assert.equal(again.el('fin-expense-group').value,'g1');assert.equal(again.el('finance-dialog-title').textContent,'替用户提交申请');
});
