const apiRecordsController = (() => {
  const $ = id => document.getElementById(id);
  const t = value => window.SiteLanguage?.t(value) || value;
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = value => typeof value==='number' && Number.isFinite(value)?value.toLocaleString(document.documentElement.lang==='en'?'en-US':'zh-CN'):'—';
  const time = value => value?new Date(value*1000).toLocaleString(document.documentElement.lang==='en'?'en-US':'zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';
  const localInput = date => new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
  const types={0:'未知',1:'充值',2:'消耗',3:'管理（历史）',4:'系统',5:'错误',6:'退款',7:'登录（历史）'};
  const actions={created:'创建密钥',revealed:'读取密钥',enabled:'启用密钥',disabled:'停用密钥',updated:'编辑限制',revoked:'撤销密钥'};
  const outcomes={pending:'处理中',succeeded:'成功',failed:'失败',uncertain:'待核实'};
  const columns={time:'时间',key:'令牌',model:'模型',stream:'流',tokens:'Token',cost:'费用',timing:'耗时',details:'详情'};
  const visibleColumns=new Set(Object.keys(columns));
  const logs={page:1,size:100,data:null,params:null,seq:0},audit={page:1,size:20,data:null,params:{},seq:0};
  let admin=false,active='overview',ownKeys=[],exportAbort=null;
  function message(id,value,success=false){$(id).textContent=value;$(id).classList.toggle('success',success);}
  async function request(url,options={}){
    const response=await fetch(url,{cache:'no-store',...options});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${response.status}`);
    return data;
  }
  function money(quota,display){
    if(typeof quota!=='number' || !Number.isFinite(quota))return '—';
    if(display?.unit==='currency' && display.divisor>0){
      const value=quota/display.divisor;
      return `${display.symbol}${value.toLocaleString(document.documentElement.lang==='en'?'en-US':'zh-CN',{maximumFractionDigits:8})}`;
    }
    return `${num(quota)} ${t('配额点')}`;
  }
  function query(state){return new URLSearchParams({...state.params,page:state.page,page_size:state.size});}
  function selectOptions(id,entries,placeholder){
    const select=$(id),selected=select.value;
    select.innerHTML=`<option value="">${esc(t(placeholder))}</option>`+entries.map(([value,label])=>`<option value="${esc(value)}" data-i18n-skip>${esc(label)}</option>`).join('');
    if(entries.some(([value])=>value===selected))select.value=selected;
  }
  function dates(range){
    const end=new Date();end.setHours(23,59,59,999);const start=new Date();start.setHours(0,0,0,0);
    if(range!=='today' && range!=='all')start.setDate(start.getDate()-Number(range)+1);
    $('api-log-start').value=range==='all'?'':localInput(start);
    $('api-log-end').value=localInput(end);
    dateLabel();
  }
  function dateLabel(){
    $('api-log-date-label').textContent=$('api-log-start').value?`${$('api-log-start').value.replace('T',' ')} ~ ${$('api-log-end').value.replace('T',' ')}`:t('全部时间');
  }
  function logParams(){
    const from=$('api-log-start').value,to=$('api-log-end').value;
    return {start_timestamp:from?Math.floor(new Date(from).getTime()/1000):0,end_timestamp:to?Math.floor(new Date(to).getTime()/1000)+59:0,
      model_name:$('api-log-search').value.trim(),group:$('api-log-group').value,type:$('api-log-status').value,
      key_id:$('api-log-key').value,request_id:$('api-log-request-id').value.trim(),upstream_request_id:$('api-log-upstream-id').value.trim()};
  }
  function pager(kind,state,host){
    if(!state.data){$(host).innerHTML='';return;}
    const total=state.data.total,pages=Math.max(1,Math.ceil(total/state.size));
    const button=(page,label,text,disabled)=>`<button type="button" data-record-kind="${kind}" data-record-page="${page}" aria-label="${esc(t(label))}" ${disabled?'disabled':''}>${text}</button>`;
    const from=Math.max(1,state.page-1),to=Math.min(pages,state.page+1),numbers=[];
    for(let page=from;page<=to;page++)numbers.push(`<button type="button" data-record-kind="${kind}" data-record-page="${page}" aria-label="${esc(t('前往页面')+' '+page)}" ${page===state.page?'aria-current="page"':''}>${page}</button>`);
    $(host).innerHTML=`<span>${esc(t('总计'))}: ${num(total)}</span><div class="api-pager-controls"><label>${esc(t('每页行数'))}<select data-record-size="${kind}" aria-label="${esc(t('每页行数'))}">${[20,50,100].map(size=>`<option value="${size}" ${size===state.size?'selected':''}>${size}</option>`).join('')}</select></label>${button(1,'首页','«',state.page===1)}${button(state.page-1,'上一页','‹',state.page===1)}${numbers.join('')}${button(state.page+1,'下一页','›',state.page>=pages)}${button(pages,'末页','»',state.page>=pages)}</div>`;
  }
  function renderLogs(){
    if(!logs.data)return;
    const data=logs.data,stats=data.stats;
    $('api-log-quota').textContent=money(stats?.quota ?? data.items.reduce((sum,row)=>sum+(row.quota || 0),0),data.display);
    $('api-log-rpm').textContent=num(stats?.rpm);$('api-log-tpm').textContent=num(stats?.tpm);
    $('api-log-stats-note').textContent=stats?'':t('用量为当前页统计');
    selectOptions('api-log-group',data.groups.map(value=>[value,value]),'全部分组');
    $('api-usage-rows').innerHTML=data.items.map(row=>{
      const speed=row.use_time>0?Math.round(row.completion_tokens/row.use_time):null;
      return `<tr><td data-log-col="time"><time data-i18n-skip>${esc(time(row.created_at))}</time><small>${esc(t(types[row.type] || '未知'))}</small></td><td data-log-col="key"><span data-i18n-skip>${esc(row.token_name)}</span><small data-i18n-skip>${esc(row.group || '—')}</small></td><td data-log-col="model"><button type="button" class="api-link api-model-id" data-model-copy="${esc(row.model_name || '')}">${esc(row.model_name || '—')}</button></td><td data-log-col="stream"><span class="api-flow-tag">${esc(t(row.is_stream?'流式':'非流'))}</span><small title="${esc(t('近似输出速度'))}">${speed===null?'—':num(speed)+' t/s'}</small></td><td data-log-col="tokens" class="api-number" title="${esc(t('输入 / 输出'))}">${num(row.prompt_tokens)} / ${num(row.completion_tokens)}</td><td data-log-col="cost" class="api-number">${esc(money(row.quota,data.display))}</td><td data-log-col="timing"><span>${esc(t('耗时'))} ${row.use_time===0?'&lt;1s':num(row.use_time)+'s'}</span>${typeof row.billing?.frt==='number'?`<small>${esc(t('首响'))} ${num(row.billing.frt)}ms</small>`:''}</td><td data-log-col="details"><button type="button" class="api-link" data-log-detail="${esc(row.id)}">${esc(t('详情'))}</button></td></tr>`;
    }).join('') || `<tr><td colspan="${visibleColumns.size}" class="api-empty"><strong>${esc(t('未找到日志'))}</strong><small>${esc(t('当前条件下没有使用日志。调整筛选或发起调用后再查看。'))}</small></td></tr>`;
    document.querySelectorAll('[data-log-col]').forEach(node=>{node.hidden=!visibleColumns.has(node.dataset.logCol);});
    pager('logs',logs,'api-log-pager');
  }
  function renderAudit(){
    if(!audit.data)return;
    $('api-audit-rows').innerHTML=audit.data.items.map(row=>`<tr><td><time data-i18n-skip>${esc(new Date(row.created_at).toLocaleString(document.documentElement.lang==='en'?'en-US':'zh-CN'))}</time></td><td data-i18n-skip>${esc(row.actor_name)}</td><td>${esc(t(actions[row.action] || row.action))}</td><td><span data-i18n-skip>${esc(row.key_name || '—')}</span><small data-i18n-skip>${esc(row.key_prefix?row.key_prefix+'…':row.key_id || '—')}</small></td><td data-i18n-skip>${esc(row.owner_name || row.owner_id || '—')}</td><td><span class="${row.outcome==='succeeded'?'api-good':row.outcome==='failed'?'api-bad':'api-dim'}">${esc(t(outcomes[row.outcome] || row.outcome))}</span></td></tr>`).join('') || `<tr><td colspan="6" class="api-empty">${esc(t('没有符合条件的审计事件。'))}</td></tr>`;
    pager('audit',audit,'api-audit-pager');
  }
  async function load(kind){
    if(kind==='models')return apiModelCatalog.load();
    if(kind==='audit' && !admin)return;
    const state={logs,audit}[kind];if(!state)return;
    state.abort?.abort();state.abort=new AbortController();const seq=++state.seq;state.loading=true;
    const feedback=kind==='logs'?'api-log-feedback':'api-audit-feedback';
    message(feedback,t('加载中…'));
    const submit=$(kind==='logs'?'api-log-submit':'api-audit-submit');
    submit.disabled=true;
    try{
      const data=await request(kind==='audit'?`/api/admin/api2api/audit?${query(audit)}`:`/api/api2api/logs?${query(logs)}`,{method:'GET',signal:state.abort.signal});
      if(seq!==state.seq || !$('view-api2api').isConnected)return;
      state.data=data;message(feedback,'');
      if(kind==='logs')renderLogs();else if(kind==='audit')renderAudit();
    }catch(error){if(seq===state.seq && error.name!=='AbortError'){state.data=null;message(feedback,t(error.message));if(kind==='logs'){$('api-usage-rows').innerHTML=`<tr><td colspan="8" class="api-empty">${esc(t('日志加载失败，请重试。'))}</td></tr>`;$('api-log-pager').innerHTML='';for(const id of ['api-log-quota','api-log-rpm','api-log-tpm'])$(id).textContent='—';$('api-log-stats-note').textContent='';}else if(kind==='audit'){$('api-audit-rows').innerHTML='';$('api-audit-pager').innerHTML='';}}}
    finally{if(seq===state.seq){state.loading=false;submit.disabled=false;}}
  }
  function activate(kind){active=kind;if(kind==='logs' && !logs.params){dates('today');logs.params=logParams();}const state={logs,audit}[kind];if(kind==='models' || (state && !state.loading))return load(kind);}
  $('api-log-form').addEventListener('submit',event=>{event.preventDefault();logs.params=logParams();logs.page=1;load('logs');});
  $('api-log-more').addEventListener('click',()=>{const open=$('api-log-extra').hidden;$('api-log-extra').hidden=!open;$('api-log-more').setAttribute('aria-expanded',String(open));$('api-log-more').textContent=t(open?'收起':'展开');});
  document.querySelectorAll('[data-log-range]').forEach(button=>button.addEventListener('click',()=>{dates(button.dataset.logRange);$('api-log-dates').open=false;}));
  $('api-log-apply-dates').addEventListener('click',()=>{dateLabel();$('api-log-dates').open=false;});
  $('api-log-reset').addEventListener('click',()=>{$('api-log-form').reset();$('api-log-dates').open=false;dates('today');logs.page=1;logs.params=logParams();load('logs');});
  $('api-audit-form').addEventListener('submit',event=>{event.preventDefault();audit.page=1;audit.params={q:$('api-audit-search').value.trim(),action:$('api-audit-action').value,outcome:$('api-audit-outcome').value,start:$('api-audit-start').value?new Date($('api-audit-start').value).toISOString():'',end:$('api-audit-end').value?new Date($('api-audit-end').value).toISOString():''};load('audit');});
  $('api-audit-reset').addEventListener('click',()=>{$('api-audit-form').reset();audit.params={};audit.page=1;load('audit');});
  $('api-log-column-options').innerHTML=Object.entries(columns).map(([key,label])=>`<label><input type="checkbox" data-log-column="${key}" checked ${key==='time'?'disabled':''} /><span ${key==='tokens'?'data-i18n-skip':''}>${esc(key==='tokens'?label:t(label))}</span></label>`).join('');
  $('api-log-column-options').addEventListener('change',event=>{const key=event.target.dataset.logColumn;if(!key)return;if(event.target.checked)visibleColumns.add(key);else visibleColumns.delete(key);renderLogs();});
  $('view-api2api').addEventListener('change',event=>{const kind=event.target.dataset.recordSize,state={logs,audit}[kind];if(state){state.size=Number(event.target.value);state.page=1;load(kind);}});
  $('view-api2api').addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button || button.disabled)return;
    const kind=button.dataset.recordKind,state={logs,audit}[kind];
    if(state){state.page=Number(button.dataset.recordPage);load(kind);return;}
    if(button.dataset.modelCopy){try{await navigator.clipboard.writeText(button.dataset.modelCopy);message('api-log-feedback',t('模型 ID 已复制'),true);}catch{message('api-log-feedback',t('请选中模型 ID 手动复制'));}}
    if(button.dataset.logDetail){
      const row=logs.data?.items.find(row=>String(row.id)===button.dataset.logDetail);if(!row)return;
      const fields=[['请求 ID',row.request_id],['上游请求 ID',row.upstream_request_id],['令牌',row.token_name],['模型',row.model_name],['分组',row.group],['时间',time(row.created_at)],['类型',t(types[row.type] || '未知')],['流',t(row.is_stream?'流式':'非流')],['输入 Token',num(row.prompt_tokens)],['输出 Token',num(row.completion_tokens)],['总费用',money(row.quota,logs.data.display)],['耗时',num(row.use_time)+'s']];
      for(const [name,label] of Object.entries({frt:'首响（ms）',model_ratio:'模型倍率',completion_ratio:'输出倍率',group_ratio:'分组倍率',user_group_ratio:'用户分组倍率',model_price:'按次价格',cache_tokens:'缓存输入 Token',cache_ratio:'缓存倍率',cache_creation_tokens:'缓存写入 Token',cache_creation_ratio:'缓存写入倍率'}))if(typeof row.billing?.[name]==='number')fields.push([label,num(row.billing[name])]);
      $('api-log-detail').innerHTML=fields.map(([label,value])=>`<div><dt>${esc(t(label))}</dt><dd data-i18n-skip>${esc(value || '—')}</dd></div>`).join('');$('api-log-detail-dialog').showModal();
    }
    if(button.dataset.logExport){
      if(exportAbort)return;const format=button.dataset.logExport;exportAbort=new AbortController();
      document.querySelectorAll('[data-log-export]').forEach(node=>{node.disabled=true;});$('api-log-export-menu').open=false;
      message('api-log-feedback',t('正在导出全部匹配日志…'));
      try{const response=await fetch(`/api/api2api/logs/export?${new URLSearchParams({...logs.params,format})}`,{cache:'no-store',signal:exportAbort.signal});if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.detail || `HTTP ${response.status}`);}const blob=await response.blob();if(!$('view-api2api').isConnected || window.location.hash!=='#view-api2api')return;const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='api-usage-logs.'+format;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);message('api-log-feedback',`${response.headers.get('X-Export-Count') || ''} ${t('条日志已导出')}`,true);}
      catch(error){if(error.name!=='AbortError')message('api-log-feedback',t(error.message));}
      finally{exportAbort=null;document.querySelectorAll('[data-log-export]').forEach(node=>{node.disabled=false;});}
    }
  });
  document.addEventListener('click',event=>{document.querySelectorAll('.api-inline-menu[open],.api-date-picker[open]').forEach(node=>{if(!node.contains(event.target))node.open=false;});});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')document.querySelectorAll('.api-inline-menu[open],.api-date-picker[open]').forEach(node=>{node.open=false;});});
  function cancel(){for(const state of [logs,audit])state.abort?.abort();exportAbort?.abort();apiModelCatalog.cancel();}
  window.addEventListener('pagehide',cancel);window.addEventListener('hashchange',()=>{if(window.location.hash!=='#view-api2api')cancel();});
  document.addEventListener('site:language',()=>{dateLabel();renderLogs();renderAudit();});
  return {start(value){admin=value;$('api-audit-tab').hidden=!value;if(!value){audit.abort?.abort();audit.seq++;audit.data=null;audit.loading=false;$('api-audit-rows').innerHTML='';$('api-audit-pager').innerHTML='';}},activate,refresh(){if(active==='logs' && !logs.params){dates('today');logs.params=logParams();}if({logs,audit}[active]?.loading)return;return load(active);},setKeys(rows){ownKeys=rows;selectOptions('api-log-key',ownKeys.filter(row=>row.provider==='newapi' && row.available_here!==false).map(row=>[row.id,`${row.name} · ${row.key_prefix}…`]),'全部令牌');}};
})();
