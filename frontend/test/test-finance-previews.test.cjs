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
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};dialog.open=true;
  const host=w.document.createElement('section');host.id='fin-detail-previews';w.document.getElementById('fin-detail-content').append(host);
  const calls=[],viewers=[];w.matchMedia=()=>({matches:false});
  w.fetch=async(url,options)=>{calls.push([url,options]);return handler?handler(url,options):{ok:true,json:async()=>({pages:[1,2].map(page=>({src:'/preview?page='+page,thumbnail:'/thumb?page='+page,width:1000,height:700}))})};};
  let loads=0;w.PagePerformance={loadScript:async()=>{loads++;}};
  w.PhotoSwipe=class{constructor(options){this.options=options;this.listeners={};this.currSlide={toggleZoom:()=>{this.zoomed=!this.zoomed;}};viewers.push(this);}on(event,handler){this.listeners[event]=handler;}init(){this.open=true;}destroy(){this.open=false;this.listeners.destroy?.();}close(){this.destroy();}};
  w.eval(fs.readFileSync(path.join(root,'test-finance-previews.js'),'utf8'));
  return {w,dialog,host,calls,viewers,loads:()=>loads};
}
test('invoice thumbnails lazy-load the existing zoom viewer with all PDF pages and named controls',async t=>{
  const {w,dialog,host,viewers,loads,calls}=setup(t);await w.FinancePreviews.render(item,{id:'event-1'});
  assert.equal(calls[0][0],'/api/finance/applications/app-1/invoice-preview?event_id=event-1');
  assert.equal(host.querySelectorAll('img').length,2);assert.equal(loads(),0);
  host.querySelectorAll('button')[1].click();await tick();await tick();
  assert.equal(loads(),1);assert.equal(viewers[0].options.index,1);assert.equal(viewers[0].options.dataSource.length,2);
  const layer=w.document.getElementById('fin-invoice-viewer');
  assert.equal(viewers[0].options.appendToEl,layer);assert.equal(dialog.contains(layer),false);assert.equal(layer.open,true);
  assert.equal(viewers[0].options.closeTitle,'关闭大图');assert.equal(viewers[0].options.initialZoomLevel,'fit');
  assert.equal(viewers[0].options.imageClickAction,'zoom');assert.equal(viewers[0].options.bgClickAction,'close');
  assert.equal(viewers[0].options.dataSource[1].src,'/preview?page=2');
  const cancel=new w.Event('cancel',{cancelable:true});layer.dispatchEvent(cancel);
  assert.equal(cancel.defaultPrevented,true);assert.equal(viewers[0].open,false);assert.equal(dialog.open,true);
  assert.equal(layer.open,false);
  host.querySelector('button').click();await tick();await tick();assert.equal(loads(),1);
  dialog.close();assert.equal(viewers[1].open,false);
  assert.equal(layer.open,false);
});
test('touching the image zooms while touching its background closes only the image layer',async t=>{
  const {w,dialog,host,viewers}=setup(t);await w.FinancePreviews.render(item);
  host.querySelector('button').click();await tick();await tick();
  const viewer=viewers[0],image=w.document.createElement('img');image.className='pswp__img';
  viewer.options.tapAction.call(viewer,{x:10,y:10},{target:image});
  assert.equal(viewer.zoomed,true);assert.equal(viewer.open,true);
  viewer.options.tapAction.call(viewer,{x:2,y:2},{target:w.document.createElement('div')});
  assert.equal(viewer.open,false);assert.equal(dialog.open,true);
  assert.equal(w.document.getElementById('fin-invoice-viewer').open,false);
});
test('a pending viewer cannot open after the application details are closed',async t=>{
  const {w,dialog,host,viewers}=setup(t);let ready;w.PagePerformance.loadScript=()=>new Promise(resolve=>{ready=resolve;});
  await w.FinancePreviews.render(item);host.querySelector('button').click();await tick();
  dialog.close();ready();await tick();await tick();
  assert.equal(viewers.length,0);assert.equal(w.document.getElementById('fin-invoice-viewer').open,false);
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
