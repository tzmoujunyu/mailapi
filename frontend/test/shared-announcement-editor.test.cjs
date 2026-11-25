const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'../../mailapi_shared/static');
const read=name=>fs.readFileSync(path.join(root,name),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const image={url:'/api/content-images/site/'+'c'.repeat(32)+'.webp',preview:'/api/content-images/site/'+'c'.repeat(32)+'.preview.webp',width:640,height:320};

async function fixture(t,{admin=true}={}){
  const dom=new JSDOM('<html lang="zh-CN"><body><div class="test-ui-header-actions"></div></body></html>',{url:'https://example.test',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,calls=[],state={fail:false,editor:null};
  t.after(()=>{w.SiteAnnouncementEditor?.destroy();w.close();});
  w.matchMedia=()=>({matches:true});w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
  w.Range.prototype.getClientRects=()=>[];w.Range.prototype.getBoundingClientRect=()=>({left:0,top:0,right:0,bottom:0,width:0,height:0});
  const item={id:'note',title:'网站更新',body:'旧正文',revision:4,updated_at:'2026-11-24T00:00:00Z',pinned:false,
    blocks:[{type:'paragraph',data:{text:'<b>旧正文</b>'}},{type:'image',data:{file:image,caption:'说明'}}],doc_version:1};
  w.fetch=async(url,options={})=>{
    calls.push([url,options]);
    if(options.method==='PUT'||options.method==='POST'&&url==='/api/admin/site-announcements')return{ok:!state.fail,json:async()=>state.fail?{detail:'公告已被其他管理员修改，请刷新'}:{item}};
    return{ok:true,json:async()=>url.startsWith('/api/finance/')?{items:[],unread_count:0,total:0}:{items:[item],can_manage:admin}};
  };
  for(const name of ['vendor/tiptap/tiptap.js','codex-tip-content.js','codex-tip-format.js','shared-content-editor.js','shared-content-dialog.js','shared-announcement-editor.js'])w.eval(read(name));
  const RealEditor=w.MailapiTiptap.Editor;
  w.MailapiTiptap={...w.MailapiTiptap,Editor:class extends RealEditor{constructor(options){super(options);state.editor=this;}}};
  w.PhotoSwipe=class{};
  w.eval(read('test-notifications.js'));await tick();
  return{w,state,item,calls,el:id=>w.document.getElementById(id)};
}

test('website announcements submit rich blocks with the displayed revision and preserve content after conflicts',async t=>{
  const {w,state,calls,el}=await fixture(t);
  w.document.querySelector('.site-notice-controls button').click();await tick();
  const dialog=el('site-notice-edit-dialog');assert.ok(dialog.open);
  assert.equal(el('site-notice-title').value,'网站更新');assert.equal(dialog.querySelectorAll('.ProseMirror figure').length,1);
  el('site-notice-title').value='修改标题';el('site-notice-pinned').checked=true;
  state.fail=true;el('site-notice-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.ok(dialog.open);assert.match(el('site-notice-error').textContent,/其他管理员修改/);
  assert.equal(el('site-notice-title').value,'修改标题');assert.equal(dialog.querySelectorAll('.ProseMirror figure').length,1);
  assert.equal(el('site-notice-save').disabled,false);assert.equal(el('site-notice-form').inert,false);
  state.fail=false;el('site-notice-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  const payload=JSON.parse(calls.filter(([url,options])=>url==='/api/admin/site-announcements/note'&&options.method==='PUT').at(-1)[1].body);
  assert.equal(payload.revision,4);assert.equal(payload.doc_version,1);assert.equal(payload.pinned,true);assert.equal(payload.title,'修改标题');
  assert.equal(payload.blocks.find(block=>block.type==='image').data.file.url,image.url);assert.equal('body' in payload,false);assert.equal(dialog.open,false);
});

test('a new website announcement saves through the same editor and readonly users only receive reading controls',async t=>{
  const admin=await fixture(t);admin.w.document.querySelector('[data-create]').click();await tick();
  admin.el('site-notice-title').value='新公告';admin.state.editor.commands.setContent('<p>新的<b>内容</b></p>');
  admin.el('site-notice-form').dispatchEvent(new admin.w.Event('submit',{cancelable:true}));await tick();
  const payload=JSON.parse(admin.calls.find(([url,options])=>url==='/api/admin/site-announcements'&&options.method==='POST')[1].body);
  assert.equal(payload.blocks[0].data.text,'新的<b>内容</b>');assert.equal('revision' in payload,false);
  const user=await fixture(t,{admin:false});assert.equal(user.w.document.querySelector('[data-create]').hidden,true);
  assert.equal(user.w.document.querySelector('.site-notice-controls'),null);
  user.w.document.querySelector('.site-notice-title').click();await tick();
  assert.ok(user.w.document.querySelector('[data-body] b'));assert.equal(user.w.document.querySelector('[data-body] img').getAttribute('src'),image.url);
  assert.equal(user.w.SiteAnnouncementEditor,undefined);
});

test('announcement-only changes require confirmation before navigation and discard closes the global dialog',async t=>{
  const {w,el}=await fixture(t);w.document.querySelector('[data-create]').click();await tick();
  el('site-notice-pinned').checked=true;
  const blocked=w.SiteAnnouncementEditor.canLeave();el('site-notice-keep-editing').click();assert.equal(await blocked,false);
  const allowed=w.SiteAnnouncementEditor.canLeave();el('site-notice-discard').click();assert.equal(await allowed,true);
  assert.equal(el('site-notice-edit-dialog').open,false);
});
