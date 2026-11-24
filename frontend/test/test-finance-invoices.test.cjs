const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const assets=path.resolve(__dirname,'../../mailapi_shared/static');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const fields={number:'20261122000000000001',code:'',date:'2026-11-22',total:'123.45',check_code:''};
const item={id:'app-1',username:'alice',project:'测试<发票>',version:2,status:'pending',invoice:{file_id:'file-1',fields,recognition:{fields},verification:{status:'unverified',revision:3}}};
function setup(t,handler){
  const dom=new JSDOM(fs.readFileSync(path.join(assets,'test-features.html'),'utf8'),{url:'http://localhost/test-features#view-finance',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,el=id=>w.document.getElementById(id),calls=[];
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
  w.fetch=async(url,options={})=>{calls.push([url,options]);return {ok:true,json:async()=>handler?handler(url,options):{item,checks:[]}};};
  w.eval(fs.readFileSync(path.join(assets,'test-finance-invoices.js'),'utf8'));
  return {w,el,calls};
}

test('invoice details require confirmation and retain leading zeros for ordinary invoice codes',t=>{
  const {w,el}=setup(t);w.FinanceInvoices.fill({...item,invoice:{fields:{...fields,number:'00001234',code:'012002600111'}}});
  assert.equal(el('fin-inv-number').value,'00001234');assert.equal(el('fin-inv-code').required,true);
  assert.throws(()=>w.FinanceInvoices.getFields(),/勾选确认/);
  el('fin-inv-confirm').checked=true;assert.equal(w.FinanceInvoices.getFields().code,'012002600111');
  el('fin-inv-number').value=fields.number;el('fin-inv-number').dispatchEvent(new w.Event('input'));
  assert.equal(el('fin-inv-code').disabled,true);assert.equal(el('fin-inv-code').value,'');assert.equal(el('fin-inv-confirm').checked,false);
});

test('late local recognition preserves manual edits and cannot populate a closed or replaced form',async t=>{
  let resolve;const {w,el}=setup(t,()=>new Promise(done=>{resolve=done;}));
  w.FinanceInvoices.fill(null);el('finance-dialog').showModal();
  Object.defineProperty(el('finance-invoice'),'files',{configurable:true,value:[new w.File(['image'],'invoice.png',{type:'image/png'})]});
  el('finance-invoice').dispatchEvent(new w.Event('change'));await tick();assert.equal(el('finance-submit').disabled,true);
  el('fin-inv-number').value='20261122000000000002';el('fin-inv-number').dispatchEvent(new w.Event('input'));
  resolve({recognition:{fields,method:'ocr',message:'请核对识别信息'}});await tick();await tick();
  assert.equal(el('fin-inv-number').value,'20261122000000000002');assert.equal(el('fin-inv-total').value,'123.45');assert.equal(el('finance-submit').disabled,false);
  el('fin-inv-recognize').click();await tick();el('finance-dialog').close();w.FinanceInvoices.fill(null);
  resolve({recognition:{fields,method:'ocr',message:'请核对识别信息'}});await tick();await tick();
  assert.equal(el('fin-inv-number').value,'');assert.equal(el('finance-submit').disabled,false);
});

test('clearing a file during recognition releases the submit control and discards the late result',async t=>{
  let resolve;const {w,el}=setup(t,()=>new Promise(done=>{resolve=done;}));w.FinanceInvoices.fill(null);el('finance-dialog').showModal();
  Object.defineProperty(el('finance-invoice'),'files',{configurable:true,value:[new w.File(['image'],'invoice.png')]});el('finance-invoice').dispatchEvent(new w.Event('change'));await tick();
  Object.defineProperty(el('finance-invoice'),'files',{configurable:true,value:[]});el('finance-invoice').dispatchEvent(new w.Event('change'));
  assert.equal(el('finance-submit').disabled,false);resolve({recognition:{fields,method:'ocr',message:'请核对识别信息'}});await tick();await tick();
  assert.equal(el('fin-inv-number').value,'');
});

test('recognize again refills cleared fields but preserves edits made during the new request',async t=>{
  let resolve;const {w,el,calls}=setup(t,()=>new Promise(done=>{resolve=done;}));
  w.FinanceInvoices.fill(null);el('finance-dialog').showModal();
  Object.defineProperty(el('finance-invoice'),'files',{configurable:true,value:[new w.File(['invoice'],'invoice.pdf')]});
  el('finance-invoice').dispatchEvent(new w.Event('change'));await tick();
  resolve({recognition:{fields,method:'pdf_text',message:'请核对识别信息'}});await tick();await tick();
  for(const key of ['number','date','total']){el('fin-inv-'+key).value='';el('fin-inv-'+key).dispatchEvent(new w.Event('input'));}
  el('fin-inv-confirm').checked=true;el('fin-inv-recognize').click();await tick();
  assert.equal(el('fin-inv-confirm').checked,false);
  el('fin-inv-check-code').value='123456';el('fin-inv-check-code').dispatchEvent(new w.Event('input'));
  resolve({recognition:{fields,method:'pdf_text',message:'请核对识别信息'}});await tick();await tick();
  assert.equal(el('fin-inv-number').value,fields.number);assert.equal(el('fin-inv-date').value,fields.date);
  assert.equal(el('fin-inv-total').value,fields.total);assert.equal(el('fin-inv-check-code').value,'123456');
  assert.equal(el('fin-inv-status').textContent,'');assert.equal(calls.length,2);
});

test('payment requirement follows the invoice total and retains an existing nonempty attachment',async t=>{
  const {w,el}=setup(t,()=>({recognition:{fields:{...fields,total:'1000.00'},method:'ocr',message:'请核对识别信息'}}));
  w.FinanceInvoices.fill(item);el('finance-dialog').showModal();
  for(const [total,required] of [['999.99',false],['1000.00',true],['1000.01',true]]){
    el('fin-inv-total').value=total;el('fin-inv-total').dispatchEvent(new w.Event('input'));
    assert.equal(el('finance-payment').required,required);el('fin-inv-confirm').checked=true;
    if(required)assert.throws(()=>w.FinanceInvoices.getFields(),/必须上传支付记录/);
    else assert.equal(w.FinanceInvoices.getFields().total,total);
  }
  Object.defineProperty(el('finance-payment'),'files',{configurable:true,value:[new w.File(['receipt'],'receipt.png')]});
  assert.equal(w.FinanceInvoices.getFields().total,'1000.01');
  Object.defineProperty(el('finance-payment'),'files',{configurable:true,value:[]});
  w.FinanceInvoices.fill({...item,invoice:{fields:{...fields,total:'1000.00'}},files:[{kind:'payment',size:7}]});
  assert.equal(el('finance-payment').required,false);el('fin-inv-confirm').checked=true;
  assert.equal(w.FinanceInvoices.getFields().total,'1000.00');
  w.FinanceInvoices.fill(item);el('fin-inv-recognize').click();await tick();await tick();
  assert.equal(el('finance-payment').required,true);
  assert.equal(el('finance-amount'),null);assert.equal(el('fin-project-hint'),null);
  assert.equal(el('fin-payment-hint').textContent,'1000元以上必选');
});

test('ordinary users can inspect checks while only admins can record a separate tax result',async t=>{
  const {w,el,calls}=setup(t);w.FinanceInvoices.start(false);
  el('finance-rows').innerHTML=w.FinanceInvoices.summary(item);el('finance-rows').querySelector('button').click();await tick();
  assert.equal(el('fin-invoice-check-form').hidden,true);assert.match(el('fin-check-summary').textContent,/测试<发票>/);assert.equal(el('fin-check-summary').querySelector('img'),null);
  w.FinanceInvoices.start(true);el('fin-check-refresh').click();await tick();
  assert.equal(el('fin-invoice-check-form').hidden,false);assert.equal(el('fin-check-legacy').hidden,true);
  el('fin-check-result').value='verified';el('fin-check-note').value='人工查验结果';el('fin-invoice-check-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();await tick();
  const [,options]=calls.find(([url])=>url.endsWith('/invoice/verify'));assert.deepEqual(JSON.parse(options.body),{status:'verified',note:'人工查验结果',version:2,revision:3});
  assert.ok(!calls.some(([url])=>url.endsWith('/review')));assert.equal(el('fin-check-save').disabled,false);
});
