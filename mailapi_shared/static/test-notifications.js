(() => {
  const actions = document.querySelector('.test-ui-header-actions');
  if (!actions) return;
  const translate = text => window.SiteLanguage?.t(text) || text;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'header-icon';
  button.id = 'header-notifications';
  button.setAttribute('aria-label', '通知中心');
  button.setAttribute('aria-expanded', 'false');
  button.setAttribute('aria-controls', 'header-notice-panel');
  button.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24"><use href="/static/test-navigation.svg#bell"></use></svg><span class="notification-count" hidden></span>';
  actions.prepend(button);
  const panel = document.createElement('section');
  panel.id = 'header-notice-panel';
  panel.className = 'header-notice-panel';
  panel.hidden = true;
  panel.setAttribute('aria-label', '通知中心');
  panel.innerHTML = '<div class="notice-panel-tabs" role="group" aria-label="通知分类"><button type="button" data-notice-tab="announcements" aria-pressed="true">网站公告</button><button type="button" data-notice-tab="finance" aria-pressed="false">报销通知</button></div><section data-notice-section="announcements"><div class="notice-panel-heading"><h2>网站公告</h2><button type="button" data-refresh>刷新</button></div><div class="notice-panel-actions"><button type="button" data-read-all>全部已读</button><button type="button" data-create hidden>发布公告</button></div><p role="status" class="notice-error"></p><div class="notice-items"></div></section><section data-notice-section="finance" hidden><div class="notice-panel-heading"><h2>报销通知</h2><button type="button" data-finance-refresh>刷新</button></div><div class="notice-panel-actions"><button type="button" data-finance-read-all>全部已读</button></div><p role="status" class="notice-error" data-finance-error></p><div class="finance-notice-items"></div><div class="notice-panel-pagination"><button type="button" data-finance-prev>上一页</button><span data-finance-page></span><button type="button" data-finance-next>下一页</button></div></section>';
  actions.append(panel);
  const dialog = document.createElement('dialog');
  dialog.className = 'site-announcement-dialog';
  dialog.setAttribute('aria-labelledby', 'site-announcement-heading');
  dialog.innerHTML = '<div class="notice-panel-heading"><h2 id="site-announcement-heading">公告详情</h2><button type="button" data-close aria-label="关闭">×</button></div><article class="announcement-detail" hidden><h3 data-title data-i18n-skip></h3><time></time><p data-body data-i18n-skip></p></article><form hidden><label>公告标题<input name="title" maxlength="120" required></label><label>公告内容<textarea name="body" maxlength="5000" rows="10" required></textarea></label><label class="notice-pinned"><input name="pinned" type="checkbox">置顶</label><button type="submit" class="primary">保存公告</button></form><p role="status" class="notice-error"></p>';
  document.body.append(dialog);
  const status = panel.querySelector('[role="status"]');
  const dialogStatus = dialog.querySelector('[role="status"]');
  const form = dialog.querySelector('form');
  let items = [], canManage = false, editing = null, lastLoaded = 0, loading = null;
  let financeItems=[],financeUnread=0,financePage=1,financeTotal=0,financeMax=0,financeAbort=null,financeSeq=0;
  async function api(url, options = {}) {
    const response = await fetch(url, {cache: 'no-store', ...options});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '操作失败，请重试');
    return data;
  }
  const json = (method, value) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
  function setOpen(open) {
    panel.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
  }
  function render() {
    const unread = items.filter(item => item.unread).length;
    renderCount();
    panel.querySelector('[data-create]').hidden = !canManage;
    panel.querySelector('[data-read-all]').disabled = unread === 0;
    const list = panel.querySelector('.notice-items');
    list.replaceChildren();
    if (!items.length) { list.textContent = translate('暂无网站公告。'); return; }
    for (const item of items) {
      const row = document.createElement('article');
      row.className = 'site-notice-row';
      const read = document.createElement('button');
      read.type = 'button';
      read.className = 'site-notice-title';
      const title = document.createElement('strong');
      title.setAttribute('data-i18n-skip', '');
      title.textContent = item.title;
      read.append(title);
      if (item.unread) {
        const dot = document.createElement('span');
        dot.className = 'notice-unread-dot';
        dot.setAttribute('aria-label', '未读');
        read.append(dot);
      }
      const date = document.createElement('small');
      date.textContent = (item.pinned ? translate('置顶') + ' · ' : '') + new Date(item.updated_at).toLocaleString(document.documentElement.lang);
      read.append(date);
      read.addEventListener('click', () => openDetail(item));
      row.append(read);
      if (canManage) {
        const controls = document.createElement('div');
        controls.className = 'site-notice-controls';
        for (const [label, handler] of [['编辑', () => openEditor(item)], ['删除', () => remove(item)]]) {
          const control = document.createElement('button');
          control.type = 'button';
          control.textContent = translate(label);
          control.addEventListener('click', handler);
          controls.append(control);
        }
        row.append(controls);
      }
      list.append(row);
    }
  }
  function renderCount(){
    const announcements=items.filter(item=>item.unread).length,unread=announcements+financeUnread,count=button.querySelector('.notification-count');
    count.textContent=unread>99?'99+':unread;count.hidden=unread===0;
    for(const tab of panel.querySelectorAll('[data-notice-tab]')){
      const amount=tab.dataset.noticeTab==='finance'?financeUnread:announcements;
      tab.textContent=translate(tab.dataset.noticeTab==='finance'?'报销通知':'网站公告')+(amount?' · '+amount:'');
    }
  }
  function selectNoticeTab(value){
    for(const tab of panel.querySelectorAll('[data-notice-tab]'))tab.setAttribute('aria-pressed',String(tab.dataset.noticeTab===value));
    for(const section of panel.querySelectorAll('[data-notice-section]'))section.hidden=section.dataset.noticeSection!==value;
    if(value==='finance')loadFinance(financePage);
  }
  function renderFinance(){
    const list=panel.querySelector('.finance-notice-items');list.replaceChildren();
    for(const item of financeItems){
      if(!['submitted','reviewed','overdue'].includes(item.kind))continue;
      const row=document.createElement('article');row.className='site-notice-row';
      const read=document.createElement('button');read.type='button';read.className='site-notice-title';
      const title=document.createElement('strong'),payload=item.payload;
      title.textContent=translate(item.kind==='overdue'?'报销申请已待审核超过 7 天':item.kind==='submitted'?(payload.result==='resubmitted'?'报销申请已修改重提':'收到新的报销申请'):payload.re_review?'报销审批结果已更新':payload.result==='approved'?'报销申请已通过':'报销申请已驳回');
      read.append(title);
      if(item.unread){const dot=document.createElement('span');dot.className='notice-unread-dot';dot.setAttribute('aria-label',translate('未读'));read.append(dot);}
      const summary=document.createElement('span');summary.className='finance-notice-summary';summary.setAttribute('data-i18n-skip','');summary.textContent=payload.project+' · ¥'+payload.amount;read.append(summary);
      const date=document.createElement('small');date.textContent=new Date(item.created_at).toLocaleString(document.documentElement.lang);read.append(date);
      if(payload.note){const note=document.createElement('p');note.className='finance-notice-note';note.setAttribute('data-i18n-skip','');note.textContent=payload.note;read.append(note);}
      if(item.current_version!==item.version||(item.kind==='reviewed'&&payload.result!==item.current_status)){
        const state=document.createElement('small');state.textContent=translate('申请当前状态')+': '+translate({pending:'待审核',approved:'已通过',rejected:'已驳回',cancelled:'已撤销'}[item.current_status]||'');read.append(state);
      }
      read.addEventListener('click',()=>openFinance(item));row.append(read);list.append(row);
    }
    if(!list.children.length)list.textContent=translate('暂无报销通知');
    panel.querySelector('[data-finance-read-all]').disabled=financeUnread===0;
    panel.querySelector('[data-finance-prev]').disabled=financePage<=1;
    panel.querySelector('[data-finance-next]').disabled=financePage*20>=financeTotal;
    panel.querySelector('[data-finance-page]').textContent=financePage+' / '+Math.max(1,Math.ceil(financeTotal/20));renderCount();
  }
  async function loadFinance(page=financePage,quiet=false){
    financeAbort?.abort();const abort=new AbortController(),seq=++financeSeq;financeAbort=abort;
    const requested=quiet&&financePage>1?1:page;
    try{
      const data=await api('/api/finance/notifications?page='+requested,{signal:abort.signal});if(seq!==financeSeq||abort.signal.aborted)return;
      financeUnread=data.unread_count||0;financeTotal=data.total||0;financeMax=data.max_id||0;
      if(!(quiet&&financePage>1)){
        const last=Math.max(1,Math.ceil(financeTotal/20));if(page>last){return loadFinance(last);}
        financePage=page;financeItems=(data.items||[]).filter(item=>item.payload&&item.application_id);renderFinance();
      }else renderCount();
      panel.querySelector('[data-finance-error]').textContent='';
    }catch(error){if(!abort.signal.aborted&&seq===financeSeq){financeItems=[];financeUnread=financeTotal=0;financePage=1;renderFinance();panel.querySelector('[data-finance-error]').textContent=translate('报销通知加载失败，请刷新重试');}}
  }
  async function openFinance(item){
    try{await api('/api/finance/notifications/read',json('POST',{ids:[item.id]}));loadFinance(financePage);setOpen(false);
      if(window.TestNavigation){await window.TestNavigation.navigate('/test-features#view-finance');document.dispatchEvent(new CustomEvent('finance:open-application',{detail:{id:item.application_id}}));}
      else location.href='/test-features#view-finance';
    }catch(error){panel.querySelector('[data-finance-error]').textContent=error.message;}
  }
  async function load() {
    if (loading) return loading;
    loading = api('/api/site-announcements').then(data => {
      items = data.items;
      canManage = data.can_manage;
      lastLoaded = Date.now();
      status.textContent = '';
      render();
    }).catch(error => { status.textContent = error.message; }).finally(() => { loading = null; });
    return loading;
  }
  async function markRead(values) {
    for (let offset = 0; offset < values.length; offset += 100) {
      await api('/api/site-announcements/read', json('POST', {items: values.slice(offset, offset + 100).map(({id, revision}) => ({id, revision}))}));
    }
    await load();
  }
  function prepareDialog() {
    setOpen(false);
    dialogStatus.textContent = '';
    dialog.showModal();
  }
  function openDetail(item) {
    dialog.querySelector('form').hidden = true;
    dialog.querySelector('article').hidden = false;
    dialog.querySelector('[data-title]').textContent = item.title;
    dialog.querySelector('[data-body]').textContent = item.body;
    dialog.querySelector('time').textContent = new Date(item.updated_at).toLocaleString(document.documentElement.lang);
    prepareDialog();
    markRead([item]).catch(error => { dialogStatus.textContent = error.message; });
  }
  function openEditor(item = null) {
    editing = item;
    form.reset();
    form.hidden = false;
    dialog.querySelector('article').hidden = true;
    form.elements.title.value = item?.title || '';
    form.elements.body.value = item?.body || '';
    form.elements.pinned.checked = item?.pinned || false;
    prepareDialog();
    form.elements.title.focus();
  }
  async function remove(item) {
    if (!confirm(translate('删除这条网站公告？'))) return;
    try { await api('/api/admin/site-announcements/' + encodeURIComponent(item.id), {method: 'DELETE'}); await load(); }
    catch (error) { status.textContent = error.message; }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const value = {title: form.elements.title.value, body: form.elements.body.value, pinned: form.elements.pinned.checked};
      if (editing) value.revision = editing.revision;
      await api('/api/admin/site-announcements' + (editing ? '/' + encodeURIComponent(editing.id) : ''), json(editing ? 'PUT' : 'POST', value));
      dialog.close();
      await load();
    } catch (error) { dialogStatus.textContent = error.message; }
    finally { submit.disabled = false; }
  });
  button.addEventListener('click', () => { setOpen(panel.hidden); if(!panel.hidden){if(financeUnread&&!items.some(item=>item.unread))selectNoticeTab('finance');else loadFinance(financePage);if(Date.now()-lastLoaded>300000)load();} });
  panel.querySelectorAll('[data-notice-tab]').forEach(tab=>tab.addEventListener('click',()=>selectNoticeTab(tab.dataset.noticeTab)));
  panel.querySelector('[data-finance-refresh]').addEventListener('click',()=>loadFinance(1));
  panel.querySelector('[data-finance-prev]').addEventListener('click',()=>loadFinance(financePage-1));
  panel.querySelector('[data-finance-next]').addEventListener('click',()=>loadFinance(financePage+1));
  panel.querySelector('[data-finance-read-all]').addEventListener('click',async()=>{try{await api('/api/finance/notifications/read',json('POST',{through_id:financeMax}));await loadFinance(financePage);}catch(error){panel.querySelector('[data-finance-error]').textContent=error.message;}});
  panel.querySelector('[data-refresh]').addEventListener('click', load);
  panel.querySelector('[data-create]').addEventListener('click', () => openEditor());
  panel.querySelector('[data-read-all]').addEventListener('click', () => markRead(items.filter(item => item.unread)).catch(error => { status.textContent = error.message; }));
  dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => { form.reset(); dialog.querySelector('[data-body]').textContent = ''; button.focus(); });
  document.addEventListener('click', event => { if (!panel.contains(event.target) && !button.contains(event.target)) setOpen(false); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) { setOpen(false); button.focus(); } });
  document.addEventListener('site:language',()=>{render();renderFinance();});
  document.addEventListener('finance:changed',()=>loadFinance(financePage));
  document.addEventListener('finance:notifications-changed',()=>loadFinance(financePage));
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadFinance(financePage,true);});
  window.addEventListener('focus',()=>loadFinance(financePage,true));
  window.addEventListener('pagehide',()=>{financeAbort?.abort();});
  window.setInterval(()=>{if(!document.hidden)loadFinance(financePage,true);},30000);
  load();
  loadFinance();
})();
