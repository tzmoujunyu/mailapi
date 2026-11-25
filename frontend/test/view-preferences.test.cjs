const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(path.resolve(__dirname,'../../mailapi_shared/static/view-preferences.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function setup(t, handler){
  const dom=new JSDOM('<body></body>',{url:'http://localhost/',runScripts:'outside-only'}),w=dom.window;
  t.after(()=>w.close());
  const timers=new Map(),calls=[];let id=0;
  w.setTimeout=callback=>{timers.set(++id,callback);return id;};w.clearTimeout=id=>timers.delete(id);
  w.fetch=(url,options={})=>{calls.push([url,options]);return handler(url,options);};
  w.eval(source);
  const flush=()=>{for(const [id,callback] of [...timers]){timers.delete(id);callback();}};
  return {w,calls,flush,prefs:w.ViewPreferences};
}
const response=settings=>({ok:true,json:async()=>({settings})});

test('partial updates merge in cache and in a single debounced request without losing earlier settings',async t=>{
  const {prefs,calls,flush}=setup(t,async()=>response({gptStatus:'all',gptSort:'original'}));
  await prefs.read('resources');
  prefs.save('resources',{gptStatus:'available'});prefs.save('resources',{codingSort:'quota'});
  assert.deepEqual({...await prefs.read('resources')},{gptStatus:'available',gptSort:'original',codingSort:'quota'});
  flush();await tick();
  assert.equal(calls.filter(([,opts])=>opts.method==='PUT').length,1);
  assert.deepEqual(JSON.parse(calls.at(-1)[1].body).settings,{gptStatus:'available',codingSort:'quota'});
});

test('a late read cannot replace settings changed while it was in flight',async t=>{
  let resolve;
  const {prefs}=setup(t,()=>new Promise(done=>{resolve=done;}));
  const loading=prefs.read('resources');
  prefs.save('resources',{gptStatus:'reauth'});
  resolve(response({gptStatus:'all',codingSort:'quota'}));
  assert.deepEqual({...await loading},{gptStatus:'reauth',codingSort:'quota'});
});

test('writes for a scope run in order while separate scopes can save independently',async t=>{
  let first;
  const {prefs,calls,flush}=setup(t,async(url,opts)=>{
    if(opts.method==='PUT'&&url.endsWith('/resources')&&!first)return new Promise(resolve=>{first=resolve;});
    return response({});
  });
  prefs.save('resources',{gptStatus:'available'});flush();
  prefs.save('resources',{gptStatus:'reauth'});flush();
  prefs.save('api-audit',{size:50});flush();await tick();
  assert.equal(calls.filter(([url])=>url.endsWith('/resources')).length,1);
  assert.equal(calls.filter(([url])=>url.endsWith('/api-audit')).length,1);
  first(response({}));await tick();
  assert.equal(calls.filter(([url])=>url.endsWith('/resources')).length,2);
  assert.equal(JSON.parse(calls.at(-1)[1].body).settings.gptStatus,'reauth');
});

test('page exit flushes pending preferences and failed loading is retriable',async t=>{
  let requests=0;
  const {w,prefs,calls}=setup(t,async(_url,opts)=>opts.method==='PUT'?response({}):++requests===1?{ok:false}:response({size:50}));
  assert.deepEqual({...await prefs.read('api-audit')},{});
  assert.equal((await prefs.read('api-audit')).size,50);
  prefs.save('api-audit',{size:100});w.dispatchEvent(new w.Event('pagehide'));await tick();
  assert.equal(calls.at(-1)[1].keepalive,true);
  assert.equal(JSON.parse(calls.at(-1)[1].body).settings.size,100);
});
