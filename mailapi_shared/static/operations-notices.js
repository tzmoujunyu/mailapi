/* Resource, API and backup notices share the existing bell and account session. */
(() => {
  window.OperationsNoticePanel = {create({panel,onCount,onNavigate=()=>{}}) {
    const t=value=>window.SiteLanguage?.t(value)||value;
    const tab=document.createElement('button');tab.type='button';tab.dataset.noticeTab='operations';tab.setAttribute('aria-pressed','false');
    panel.querySelector('.notice-panel-tabs').append(tab);
    const section=document.createElement('section');section.dataset.noticeSection='operations';section.hidden=true;
    section.innerHTML='<div class="notice-panel-heading"><h2>资源与 API</h2><button type="button" data-refresh>刷新</button></div><div class="notice-panel-actions"><button type="button" data-read-all>全部已读</button></div><p class="notice-error" role="status"></p><div data-backup hidden></div><div data-items></div><div class="notice-panel-pagination"><button type="button" data-prev>上一页</button><span data-page></span><button type="button" data-next>下一页</button></div>';
    panel.append(section);
    let unread=0,page=1,total=0,max=0,items=[],backup=null,loading=null,seq=0,abort=null;
    const names={expired:'资源已到期',quota:'资源额度已耗尽',authorization:'资源需要重新授权',api_quota:'API 额度已耗尽',backup:'备份或恢复验证失败'};
    async function request(url,options={}){
      const response=await fetch(url,{cache:'no-store',...options});const data=await response.json();
      if(!response.ok)throw new Error(data.detail||t('通知加载失败，请刷新重试'));return data;
    }
    const json=value=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
    function render(){
      tab.textContent=t('资源与 API')+(unread?' · '+unread:'');
      const root=section.querySelector('[data-items]');root.replaceChildren();
      for(const item of items){
        const article=document.createElement('article');article.className='site-notice-row';
        const button=document.createElement('button');button.type='button';button.className='site-notice-title';
        const title=document.createElement('strong');title.textContent=t(names[item.kind]||'通知');button.append(title);
        if(item.unread){const dot=document.createElement('span');dot.className='notice-unread-dot';dot.setAttribute('aria-label',t('未读'));button.append(dot);}
        const label=document.createElement('span');label.className='operation-notice-label';label.textContent=item.label;label.setAttribute('data-i18n-skip','');button.append(label);
        const time=document.createElement('small');time.textContent=new Date(item.created_at).toLocaleString(document.documentElement.lang);button.append(time);
        button.addEventListener('click',async()=>{
          try{
            await request('/api/operations/notifications/read',json({ids:[item.id]}));await load();
            if(item.kind==='backup')return;
            // An admin can see another user's alert without impersonating that user.
            const owner=document.querySelector('[data-test-account-id]')?.dataset.testAccountId;
            const url=item.kind==='api_quota'?'/test-features#view-api2api':'/';
            if(item.kind==='api_quota'&&owner!==item.user_id){section.querySelector('[role="status"]').textContent=t('请在 API 管理中为该用户调整额度');return;}
            onNavigate();if(window.TestNavigation)await window.TestNavigation.navigate(url);else location.href=url;
          }catch(error){section.querySelector('[role="status"]').textContent=error.message;}
        });article.append(button);root.append(article);
      }
      if(!items.length)root.textContent=t('暂无资源或 API 提醒');
      section.querySelector('[data-read-all]').disabled=!unread;
      section.querySelector('[data-prev]').disabled=page<=1;section.querySelector('[data-next]').disabled=page*20>=total;
      section.querySelector('[data-page]').textContent=page+' / '+Math.max(1,Math.ceil(total/20));
      const status=section.querySelector('[data-backup]');status.hidden=!backup;
      if(backup){
        status.replaceChildren();status.className='operation-backup-status';
        const heading=document.createElement('strong');heading.textContent=t(backup.fixture_only?'隔离样例备份演练':'备份与恢复验证');status.append(heading);
        for(const [label,value] of [['最近备份',backup.last_backup_at],['最近恢复验证',backup.last_drill_at]]){
          const row=document.createElement('p');row.textContent=t(label)+' · '+(value?new Date(value).toLocaleString(document.documentElement.lang):t('暂无记录'));status.append(row);
        }
        const note=document.createElement('small');note.textContent=t(backup.available===false?'备份状态暂时无法读取':backup.configured?'每天 03:00（北京时间），保留 30 天；每周恢复验证':'尚未配置定期备份');status.append(note);
        if(backup.fixture_only){const scope=document.createElement('p');scope.textContent=t('仅验证样例数据，当前业务数据尚未启用定期备份。');status.append(scope);}
      }
      onCount();
    }
    async function load(next=page,quiet=false){
      if(loading&&quiet)return loading;abort?.abort();const current=new AbortController(),id=++seq;abort=current;
      loading=(async()=>{try{
        const data=await request('/api/operations/notifications?page='+next,{signal:current.signal});if(id!==seq||current.signal.aborted)return;
        unread=data.unread_count||0;max=data.max_id||0;backup=data.backup_status||null;
        total=data.total||0;const last=Math.max(1,Math.ceil(total/20));if(next>last){await load(last);return;}
        items=data.items||[];page=next;
        section.querySelector('[role="status"]').textContent='';render();
      }catch(error){if(id===seq&&!current.signal.aborted){items=[];unread=total=max=0;page=1;backup=null;render();section.querySelector('[role="status"]').textContent=error.message;}}
      finally{if(id===seq)loading=null;}})();return loading;
    }
    section.querySelector('[data-refresh]').addEventListener('click',()=>load(1));
    section.querySelector('[data-prev]').addEventListener('click',()=>load(page-1));section.querySelector('[data-next]').addEventListener('click',()=>load(page+1));
    section.querySelector('[data-read-all]').addEventListener('click',async()=>{try{await request('/api/operations/notifications/read',json({through_id:max}));await load();}catch(error){section.querySelector('[role="status"]').textContent=error.message;}});
    document.addEventListener('site:language',render);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)load(page,true);});
    const timer=setInterval(()=>{if(!document.hidden)load(page,true);},60000);
    window.addEventListener('pagehide',()=>{clearInterval(timer);abort?.abort();},{once:true});
    load();return {get unread(){return unread;},load};
  }};
})();
