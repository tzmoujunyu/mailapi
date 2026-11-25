/* Server-backed, owner-only working copies, independent of submitted applications. */
window.FinanceDrafts = (() => {
  const $=id=>document.getElementById(id), t=text=>window.SiteLanguage?.t(text)||text;
  let adapter, current=null, baseline='', savedFiles={}, restored={}, timer, saving=null, submitting=false, conflict=false;
  let items=[], leaveDecision=null, restoring=false;
  const fileIds=new WeakMap();let fileSequence=0;
  const file=kind=>$(kind==='invoice'?'finance-invoice':'finance-payment').files[0]||restored[kind];
  const snapshot=()=>({fields:adapter.read(),files:{invoice:file('invoice'),payment:file('payment')}});
  const fingerprint=value=>JSON.stringify([value.fields,...['invoice','payment'].map(kind=>{
    const valueFile=value.files[kind];if(!valueFile)return null;
    if(!fileIds.has(valueFile))fileIds.set(valueFile,++fileSequence);return fileIds.get(valueFile);
  })]);
  const dirty=()=>!!current&&fingerprint(snapshot())!==baseline;
  const message=(text,error=false)=>{ $('fin-draft-status').textContent=t(text);$('fin-draft-status').classList.toggle('fin-feedback',error); };
  async function api(url,options={}) {
    const response=await fetch(url,{cache:'no-store',...options});
    const data=await response.json().catch(()=>({}));
    if(!response.ok){const error=new Error(t(data.detail||'草稿保存失败，请重试'));error.status=response.status;throw error;}
    return data;
  }
  function remember(id){try{id?sessionStorage.setItem('finance.active-draft',id):sessionStorage.removeItem('finance.active-draft');}catch{}}
  function schedule(){
    if(!current||restoring||submitting||leaveDecision)return;
    clearTimeout(timer);
    if(dirty()){
      message(conflict?'草稿已在其他页面更新，请关闭后重新打开草稿':'有内容尚未保存',conflict);
      if(!conflict)timer=setTimeout(()=>save().catch(()=>{}),700);
    }else message(current.revision?'草稿已保存':'填写后自动保存草稿');
  }
  async function save(){
    clearTimeout(timer);
    if(saving){await saving;return save();}
    if(!current||!dirty())return;
    if(conflict)throw new Error(t('草稿已在其他页面更新，请关闭后重新打开草稿'));
    const value=snapshot(),key=fingerprint(value),entry=current;
    const body=new FormData();body.set('fields',JSON.stringify(value.fields));body.set('revision',entry.revision);
    for(const kind of ['invoice','payment'])if(value.files[kind]&&value.files[kind]!==savedFiles[kind])body.set(kind==='invoice'?'invoice':'payment_record',value.files[kind]);
    message('正在保存草稿…');
    const abort=new AbortController(),timeout=setTimeout(()=>abort.abort(),30000);
    saving=(async()=>{
      try{
        const data=await api('/api/finance/drafts/'+encodeURIComponent(entry.id),{method:'PUT',body,signal:abort.signal});
        entry.revision=data.item.revision;baseline=key;savedFiles={...value.files};remember(entry.id);
        message(dirty()?'有内容尚未保存':'草稿已保存');
        items=[data.item,...items.filter(item=>item.id!==entry.id)];renderList();
      }catch(error){conflict=error.status===409||error.status===404;message(error.name==='AbortError'?'草稿保存超时，请重试':error.message,true);throw error;}
      finally{clearTimeout(timeout);saving=null;}
    })();
    await saving;
  }
  async function flush(){clearTimeout(timer);do{await save();}while(dirty());}
  function begin(){
    clearTimeout(timer);current={id:crypto.randomUUID(),revision:0};restored={};savedFiles={};conflict=false;
    baseline=fingerprint(snapshot());message('填写后自动保存草稿');
  }
  function reset(){clearTimeout(timer);current=null;restored={};savedFiles={};conflict=false;remember('');}
  function renderList(){
    $('fin-drafts-count').textContent=String(items.length);
    $('fin-draft-list').replaceChildren();
    for(const item of items){
      const row=document.createElement('article');row.className='fin-draft-row';
      const text=document.createElement('div'),title=document.createElement('strong'),meta=document.createElement('small');
      title.textContent=item.fields.project||t('未命名申请');title.dataset.i18nSkip='';
      meta.textContent=[t(item.fields.application_id?'编辑申请草稿':item.fields.on_behalf?'代提交草稿':'新申请草稿'),new Date(item.updated_at).toLocaleString(document.documentElement.lang)].join(' · ');
      text.append(title,meta);const actions=document.createElement('div');actions.className='actions';
      const resume=document.createElement('button');resume.type='button';resume.textContent=t('继续填写');resume.addEventListener('click',()=>restore(item.id));
      const remove=document.createElement('button');remove.type='button';remove.textContent=t('删除');remove.addEventListener('click',async()=>{
        $('fin-draft-delete-title').textContent=t('删除草稿');$('fin-draft-delete-dialog').showModal();
        $('fin-draft-delete-confirm').onclick=async()=>{
          const button=$('fin-draft-delete-confirm');button.disabled=true;
          try{await api(`/api/finance/drafts/${encodeURIComponent(item.id)}?revision=${item.revision}`,{method:'DELETE'});$('fin-draft-delete-dialog').close();await load();}
          catch(error){$('fin-draft-list-status').textContent=error.message;$('fin-draft-delete-dialog').close();}
          finally{button.disabled=false;}
        };
      });
      actions.append(resume,remove);row.append(text,actions);$('fin-draft-list').append(row);
    }
    if(!items.length)$('fin-draft-list').textContent=t('暂无草稿');
  }
  async function load(){
    const data=await api('/api/finance/drafts');items=data.items||[];renderList();
  }
  async function restore(id){
    if(restoring)return;restoring=true;$('fin-draft-list-status').textContent=t('正在恢复草稿…');
    try{
      const {item}=await api('/api/finance/drafts/'+encodeURIComponent(id));
      const files={};
      await Promise.all(item.files.map(async info=>{
        const response=await fetch(`/api/finance/drafts/${encodeURIComponent(id)}/files/${info.kind}?revision=${item.revision}`,{cache:'no-store'});
        if(!response.ok)throw new Error(t('草稿附件恢复失败，请重新打开草稿'));
        files[info.kind]=new File([await response.blob()],info.original_name,{type:info.content_type});
      }));
      await adapter.restore(item.fields,files);
      current={id:item.id,revision:item.revision};restored=files;savedFiles={...files};conflict=false;
      baseline=fingerprint(snapshot());remember(id);message('草稿已恢复，请核对后提交');
      $('fin-drafts-dialog').close();$('fin-draft-list-status').textContent='';
    }catch(error){$('fin-draft-list-status').textContent=error.message;if(!$('fin-drafts-dialog').open)$('fin-drafts-dialog').showModal();}
    finally{restoring=false;}
  }
  function decideLeave(){
    if(submitting||restoring)return Promise.resolve(false);
    if(!current||(!dirty()&&!saving))return Promise.resolve(true);
    if(leaveDecision)return leaveDecision.promise;
    clearTimeout(timer);
    let resolve;const promise=new Promise(done=>{resolve=done;});leaveDecision={promise,resolve};
    $('fin-draft-leave-status').textContent='';$('fin-draft-leave-dialog').showModal();return promise;
  }
  function resolveLeave(value){const decision=leaveDecision;leaveDecision=null;$('fin-draft-leave-dialog').close();decision?.resolve(value);if(!value)schedule();}
  $('fin-draft-stay').addEventListener('click',()=>resolveLeave(false));
  $('fin-draft-leave-dialog').addEventListener('cancel',event=>{event.preventDefault();resolveLeave(false);});
  $('fin-draft-leave-dialog').addEventListener('close',()=>{if(leaveDecision)resolveLeave(false);});
  $('fin-draft-save-close').addEventListener('click',async()=>{
    const button=$('fin-draft-save-close');button.disabled=true;
    try{await flush();resolveLeave(true);}catch(error){$('fin-draft-leave-status').textContent=t('保存未完成，请重试或继续填写');}finally{button.disabled=false;}
  });
  $('fin-draft-discard-changes').addEventListener('click',async()=>{if(saving)await saving.catch(()=>{});resolveLeave(true);});
  $('fin-draft-save').addEventListener('click',()=>flush().catch(()=>{}));
  $('fin-drafts-open').addEventListener('click',()=>{$('fin-draft-list-status').textContent='';$('fin-drafts-dialog').showModal();load().catch(error=>{$('fin-draft-list-status').textContent=error.message;});});
  $('finance-form').addEventListener('input',schedule);$('finance-form').addEventListener('change',schedule);
  document.addEventListener('finance:invoice-filled',schedule);
  $('finance-dialog').addEventListener('cancel',event=>{event.preventDefault();close();});
  $('finance-dialog').addEventListener('close',reset);
  window.addEventListener('beforeunload',event=>{if(dirty()||saving||submitting){event.preventDefault();event.returnValue='';}});
  async function close(){if(await decideLeave())$('finance-dialog').close();}
  return {begin,file,close,load,
    async canLeave(){if(!await decideLeave())return false;if($('finance-dialog').open)$('finance-dialog').close();return true;},
    async start(value){adapter=value;try{await load();let id;try{id=sessionStorage.getItem('finance.active-draft');}catch{}if(location.hash==='#view-finance'&&id&&items.some(item=>item.id===id))await restore(id);}catch(error){$('finance-status').textContent=error.message;}},
    async beforeSubmit(){submitting=true;$('finance-form').inert=true;await flush();return current?.revision?{id:current.id,revision:current.revision}:null;},
    afterSubmit(success=false){submitting=false;$('finance-form').inert=false;if(success){items=items.filter(item=>item.id!==current?.id);renderList();reset();}else if(current)schedule();}
  };
})();
