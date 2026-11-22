const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'../../mailapi_shared/static');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const item={id:'app-1',files:[{kind:'invoice',original_name:'<img src=x>.pdf'}]};
function setup(t,handler){
  const dom=new JSDOM(fs.readFileSync(path.join(root,'test-features.html'),'utf8'),{url:'http://localhost/test-features#view-finance',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());const w=dom.window,dialog=w.document.getElementById('fin-detail-dialog');
  w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};dialog.open=true;
  const host=w.document.createElement('section');host.id='fin-detail-previews';w.document.getElementById('fin-detail-content').append(host);
  const calls=[],viewers=[];w.matchMedia=()=>({matches:false});
  w.fetch=async(url,options)=>{calls.push([url,options]);return handler?handler(url,options):{ok:true,json:async()=>({pages:[1,2].map(page=>({src:'/preview?page='+page,thumbnail:'/thumb?page='+page,width:1000,height:700}))})};};
  let loads=0;w.PagePerformance={loadScript:async()=>{loads++;}};
  w.PhotoSwipe=class{constructor(options){this.options=options;this.listeners={};viewers.push(this);}on(event,handler){this.listeners[event]=handler;}init(){this.open=true;}destroy(){this.open=false;this.listeners.destroy?.();}close(){this.destroy();}};
  w.eval(fs.readFileSync(path.join(root,'test-finance-previews.js'),'utf8'));
  return {w,dialog,host,calls,viewers,loads:()=>loads};
}
test('invoice thumbnails lazy-load the existing zoom viewer with all PDF pages and named controls',async t=>{
  const {w,dialog,host,viewers,loads,calls}=setup(t);await w.FinancePreviews.render(item,{id:'event-1'});
  assert.equal(calls[0][0],'/api/finance/applications/app-1/invoice-preview?event_id=event-1');
  assert.equal(host.querySelectorAll('img').length,2);assert.equal(loads(),0);
  host.querySelectorAll('button')[1].click();await tick();await tick();
  assert.equal(loads(),1);assert.equal(viewers[0].options.index,1);assert.equal(viewers[0].options.dataSource.length,2);
  assert.equal(viewers[0].options.appendToEl,dialog);assert.equal(viewers[0].options.closeTitle,'关闭大图');
  assert.equal(viewers[0].options.dataSource[1].src,'/preview?page=2');
  const cancel=new w.Event('cancel',{cancelable:true});dialog.dispatchEvent(cancel);
  assert.equal(cancel.defaultPrevented,true);assert.equal(viewers[0].open,false);assert.equal(dialog.open,true);
  host.querySelector('button').click();await tick();await tick();assert.equal(loads(),1);
  dialog.close();assert.equal(viewers[1].open,false);
});
test('late preview responses never populate a closed dialog and failures leave the original download available',async t=>{
  let resolve;const {w,dialog,host}=setup(t,()=>new Promise(done=>{resolve=done;}));
  const pending=w.FinancePreviews.render(item);await tick();dialog.close();
  resolve({ok:true,json:async()=>({pages:[{src:'/old',thumbnail:'/old-thumb',width:100,height:100}]})});await pending;
  assert.equal(host.querySelector('img'),null);
  dialog.open=true;const link=w.document.createElement('a');link.href='/download';dialog.append(link);
  w.fetch=async()=>({ok:false,json:async()=>({detail:'无法预览此发票，请下载原文件查看；PDF 预览最多支持 5 页'})});
  await w.FinancePreviews.render(item);assert.match(host.textContent,/下载原文件/);assert.equal(link.getAttribute('href'),'/download');
});
