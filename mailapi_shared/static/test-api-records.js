const apiRecordsController = (() => {
  const $ = id => document.getElementById(id);
  const t = value => window.SiteLanguage?.t(value) || value;
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = value => typeof value==='number' && Number.isFinite(value)?value.toLocaleString(document.documentElement.lang==='en'?'en-US':'zh-CN'):'—';
  const time = value => value?new Date(value*1000).toLocaleString(document.documentElement.lang==='en'?'en-US':'zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';
  const localInput = date => new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,19);
  const types={0:'未知',1:'充值',2:'消耗',3:'管理（历史）',4:'系统',5:'错误',6:'退款',7:'登录（历史）'};
  const actions={created:'创建密钥',revealed:'读取密钥',enabled:'启用密钥',disabled:'停用密钥',updated:'编辑密钥',revoked:'撤销密钥'};
  const outcomes={pending:'处理中',succeeded:'成功',failed:'失败',uncertain:'待核实'};
  const columns={time:'时间',key:'令牌',model:'模型',stream:'流',tokens:'Token',cost:'费用',timing:'耗时',details:'详情'};
  const visibleColumns=new Set(Object.keys(columns));
  const logs={page:1,size:100,data:null,params:null,seq:0},audit={page:1,size:20,data:null,params:{},seq:0};
  let admin=false,active='overview',ownKeys=[],exportAbort=null,advancedOpen=false,sensitiveVisible=true,detailId=null,detailOpener=null,detailScroll=0;
  let preferencesReady=null, savedRange='today', preferenceTouched=false, auditPreferenceTouched=false, activationEpoch=0;
  const mobile=window.matchMedia?.('(max-width: 640px)');
  const icon=name=>`<svg aria-hidden="true" viewBox="0 0 24 24"><use href="/static/test-api-icons.svg#${name}"></use></svg>`;
  function savePreferences(){
    preferenceTouched=true;
    window.ViewPreferences?.save('api-logs',{size:logs.size,type:$('api-log-status').value,
      columns:[...visibleColumns],group:$('api-log-group').value,key_id:$('api-log-key').value,
      range:savedRange,start:savedRange==='custom'?$('api-log-start').value:'',
      end:savedRange==='custom'?$('api-log-end').value:'',sensitive:sensitiveVisible});
  }
  function restorePreferences(){
    if(!preferencesReady)preferencesReady=Promise.all([(async()=>{
      const saved=await window.ViewPreferences?.read('api-logs');
      if(!saved||preferenceTouched)return;
      if(saved.size)logs.size=saved.size;
      if([...$('api-log-status').options].some(option=>option.value===saved.type))$('api-log-status').value=saved.type;
      if(saved.columns){visibleColumns.clear();saved.columns.forEach(key=>visibleColumns.add(key));
        document.querySelectorAll('[data-log-column]').forEach(node=>{node.checked=visibleColumns.has(node.dataset.logColumn);});}
      if(typeof saved.sensitive==='boolean'){sensitiveVisible=saved.sensitive;updatePrivacy();}
      dates(saved.range&&saved.range!=='custom'?saved.range:'today');
      if(saved.range==='custom'){savedRange='custom';$('api-log-start').value=saved.start||'';$('api-log-end').value=saved.end||'';document.querySelectorAll('[data-log-range]').forEach(node=>node.setAttribute('aria-pressed','false'));dateLabel();}
      logs.params={...logParams(),group:saved.group||'',key_id:saved.key_id||''};
      for(const [id,key] of [['api-log-group','group'],['api-log-key','key_id']]){
        const select=$(id);if([...select.options].some(option=>option.value===logs.params[key]))select.value=logs.params[key];
      }
    })(),(async()=>{
      if(!admin)return;
      const saved=await window.ViewPreferences?.read('api-audit');if(!auditPreferenceTouched && saved?.size)audit.size=saved.size;
    })()]);
    return preferencesReady;
  }
  function message(id,value,success=false,tone='error'){$(id).textContent=value;$(id).classList.toggle('success',success);$(id).dataset.tone=success?'success':tone;}
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
    const select=$(id),selected=select.value || (id==='api-log-group'?logs.params?.group:id==='api-log-key'?logs.params?.key_id:'');
    select.innerHTML=`<option value="">${esc(t(placeholder))}</option>`+entries.map(([value,label])=>`<option value="${esc(value)}" data-i18n-skip>${esc(label)}</option>`).join('');
    if(entries.some(([value])=>value===selected))select.value=selected;
  }
  function dates(range){
    savedRange=range;
    const end=new Date();end.setHours(23,59,59,999);const start=new Date();start.setHours(0,0,0,0);
    if(range==='week')start.setDate(start.getDate()-(start.getDay()+6)%7);
    else if(range==='month')start.setDate(1);
    else if(range!=='today' && range!=='all')start.setDate(start.getDate()-Number(range)+1);
    $('api-log-start').value=range==='all'?'':localInput(start);
    $('api-log-end').value=range==='all'?'':localInput(end);
    document.querySelectorAll('[data-log-range]').forEach(node=>node.setAttribute('aria-pressed',String(node.dataset.logRange===range)));
    dateLabel();
  }
  function dateLabel(){
    const from=$('api-log-start').value,to=$('api-log-end').value;
    const full=from&&to?`${from.replace('T',' ')} ~ ${to.replace('T',' ')}`:from?`${t('起始时间')} ${from.replace('T',' ')}`:to?`${t('结束时间')} ${to.replace('T',' ')}`:t('全部时间');
    $('api-log-date-label').textContent=from&&to&&from.slice(0,10)===to.slice(0,10)?`${from.slice(5,10).replace('-','/')} ${from.slice(11)}–${to.slice(11)}`:full;
    const summary=$('api-log-dates').querySelector('summary');summary.title=t('选择日志时间范围')+' · '+full;summary.setAttribute('aria-label',summary.title);
  }
  function logParams(){
    const from=$('api-log-start').value,to=$('api-log-end').value;
    const start=from?Math.floor(new Date(from).getTime()/1000):0,end=to?Math.floor(new Date(to).getTime()/1000):0;
    if(!Number.isFinite(start)||!Number.isFinite(end)||(start&&end&&start>end))throw new Error(t('开始时间不能晚于结束时间'));
    return {start_timestamp:start,end_timestamp:end,
      model_name:$('api-log-search').value.trim(),group:$('api-log-group').value,type:$('api-log-status').value,
      key_id:$('api-log-key').value,request_id:$('api-log-request-id').value.trim(),upstream_request_id:$('api-log-upstream-id').value.trim()};
  }
  function updateLogQueryNote(){
    let dirty=false;
    try{dirty=Boolean(logs.params && JSON.stringify(logParams())!==JSON.stringify(logs.params));}catch{dirty=true;}
    $('api-log-query-note').textContent=dirty?t('筛选已更改，点击搜索后应用。'):'';
    const count=['model_name','group','key_id','request_id','upstream_request_id'].filter(key=>logs.params?.[key]).length+(Number(logs.params?.type)>0?1:0);
    $('api-log-filter-count').textContent=count;$('api-log-filter-count').hidden=!count;
  }
  function duration(seconds){
    if(typeof seconds!=='number'||!Number.isFinite(seconds)||seconds<0)return '—';
    if(seconds===0)return '<1s';
    return seconds<60?`${num(Number(seconds.toFixed(1)))}s`:`${Math.floor(seconds/60)}m ${Math.round(seconds%60)}s`;
  }
  const masked=value=>value?(sensitiveVisible?esc(value):'••••'):'—';
  const typeBadge=row=>`<span class="api-log-type ${row.type===5?'is-error':row.type===2?'is-consumption':''}">${esc(t(types[row.type] || '未知'))}</span>`;
  function streamInfo(row){
    if(![2,5].includes(row.type))return '—';
    const speed=row.is_stream&&row.use_time>0&&row.completion_tokens>0?Math.round(row.completion_tokens/row.use_time):null;
    return `<span class="api-flow-tag ${row.is_stream?'is-stream':''}">${esc(t(row.is_stream?'流式':'非流'))}</span>${speed===null?'':`<small title="${esc(t('近似输出速度'))}">${num(speed)} t/s</small>`}`;
  }
  function timingInfo(row){
    if(![2,5].includes(row.type))return '—';
    const first=row.billing?.frt;
    return `<span class="api-log-timing">${row.is_stream?`<span><small>${esc(t('首字'))}</small> ${typeof first==='number'&&first>0?esc(first<1000?num(first)+'ms':duration(first/1000)):'—'}</span>`:''}<span><small>${esc(t('耗时'))}</small> ${esc(duration(row.use_time))}</span></span>`;
  }
  function tokenInfo(row){
    if(![2,5].includes(row.type))return '—';
    return `<span class="api-log-token-counts"><span><small>${esc(t('输入'))}</small> ${num(row.prompt_tokens)}</span><span><small>${esc(t('输出'))}</small> ${num(row.completion_tokens)}</span>${row.billing?.cache_tokens>0?`<span><small>${esc(t('缓存读取'))}</small> ${num(row.billing.cache_tokens)}</span>`:''}${row.billing?.cache_creation_tokens>0?`<span><small>${esc(t('缓存写入'))}</small> ${num(row.billing.cache_creation_tokens)}</span>`:''}</span>`;
  }
  function emptyLogs(error=false){return `<div class="api-log-empty">${icon('file')}<strong>${esc(t(error?'日志加载失败，请重试。':'未找到日志'))}</strong><p>${esc(t(error?'可重新搜索或刷新日志。':'当前条件下没有使用日志。调整筛选或发起调用后再查看。'))}</p></div>`;}
  function pager(kind,state,host){
    if(!state.data){$(host).innerHTML='';return;}
    const total=state.data.total,pages=Math.max(1,Math.ceil(total/state.size));
    const button=(page,label,text,disabled)=>`<button type="button" data-record-kind="${kind}" data-record-page="${page}" aria-label="${esc(t(label))}" ${disabled?'disabled':''}>${text}</button>`;
    const from=Math.max(1,state.page-1),to=Math.min(pages,state.page+1),numbers=[];
    for(let page=from;page<=to;page++)numbers.push(`<button type="button" class="api-record-page-number" data-record-kind="${kind}" data-record-page="${page}" aria-label="${esc(t('前往页面')+' '+page)}" ${page===state.page?'aria-current="page"':''}>${page}</button>`);
    $(host).innerHTML=`<span>${esc(t('总计'))}: ${num(total)}</span><div class="api-pager-controls"><label><span>${esc(t('每页行数'))}</span><select data-record-size="${kind}" aria-label="${esc(t('每页行数'))}">${[20,50,100].map(size=>`<option value="${size}" ${size===state.size?'selected':''}>${size}</option>`).join('')}</select></label>${button(1,'首页','«',state.page===1)}${button(state.page-1,'上一页','‹',state.page===1)}${numbers.join('')}<span class="api-record-page-summary">${num(state.page)} / ${num(pages)}</span>${button(state.page+1,'下一页','›',state.page>=pages)}${button(pages,'末页','»',state.page>=pages)}</div>`;
  }
  function renderLogs(){
    if(!logs.data)return;
    const data=logs.data,stats=data.stats;
    $('api-log-quota').textContent=money(stats?.quota ?? data.items.reduce((sum,row)=>sum+(row.quota || 0),0),data.display);
    $('api-log-rpm').textContent=num(stats?.rpm);$('api-log-tpm').textContent=num(stats?.tpm);
    $('api-log-stats-note').textContent=stats?'':t('用量为当前页统计');
    selectOptions('api-log-group',data.groups.map(value=>[value,value]),'全部分组');
    $('api-usage-rows').innerHTML=data.items.map(row=>{
      return `<tr><td data-log-col="time"><time data-i18n-skip>${esc(time(row.created_at))}</time>${typeBadge(row)}</td><td data-log-col="key"><button type="button" class="api-log-key-name" data-log-detail="${esc(row.id)}" title="${esc(t('查看令牌与分组'))}">${icon('key')}<span data-i18n-skip>${masked(row.token_name)}</span></button><small data-i18n-skip>${masked(row.group)}</small></td><td data-log-col="model">${modelBadge(row)}</td><td data-log-col="stream">${streamInfo(row)}</td><td data-log-col="tokens" class="api-number">${tokenInfo(row)}</td><td data-log-col="cost" class="api-number"><button type="button" class="api-log-cost" data-log-detail="${esc(row.id)}" title="${esc(t('查看费用详情'))}">${esc(money(row.quota,data.display))}</button></td><td data-log-col="timing">${timingInfo(row)}</td><td data-log-col="details"><button type="button" class="api-link" data-log-detail="${esc(row.id)}">${esc(t('详情'))}${icon('chevron-right')}</button></td></tr>`;
    }).join('') || `<tr><td colspan="${visibleColumns.size}">${emptyLogs()}</td></tr>`;
    $('api-log-cards').innerHTML=data.items.map(row=>`<article class="api-log-card" role="listitem" data-log-record="${esc(row.id)}"><header><div data-log-col="model">${modelBadge(row)}</div><button type="button" class="api-log-cost" data-log-col="cost" data-log-detail="${esc(row.id)}" title="${esc(t('查看费用详情'))}">${esc(money(row.quota,data.display))}</button></header><div class="api-log-card-status" data-log-col="time">${typeBadge(row)}<time data-i18n-skip>${esc(time(row.created_at))}</time></div><div class="api-log-card-performance"><div data-log-col="stream">${streamInfo(row)}</div><div data-log-col="timing">${timingInfo(row)}</div></div><dl class="api-log-card-key" data-log-col="key"><div><dt>${esc(t('令牌'))}</dt><dd data-i18n-skip>${masked(row.token_name)}</dd></div><div><dt>${esc(t('分组'))}</dt><dd data-i18n-skip>${masked(row.group)}</dd></div></dl><div class="api-log-card-tokens" data-log-col="tokens">${tokenInfo(row)}</div><footer data-log-col="details"><button type="button" class="api-link" data-log-detail="${esc(row.id)}">${esc(t('查看详情'))}${icon('chevron-right')}</button></footer></article>`).join('') || emptyLogs();
    document.querySelectorAll('[data-log-col]').forEach(node=>{node.hidden=!visibleColumns.has(node.dataset.logCol);});
    pager('logs',logs,'api-log-pager');
    updateLogQueryNote();
    if(detailId!==null){const row=data.items.find(row=>String(row.id)===detailId);if(row)renderLogDetail(row);else $('api-log-detail-dialog').close();}
  }
  function modelBadge(row){return row.model_name?`<button type="button" class="api-log-model-badge" data-model-copy="${esc(row.model_name)}" title="${esc(t('复制模型 ID'))}" aria-label="${esc(t('复制模型 ID')+' '+row.model_name)}">${icon('model')}<span data-i18n-skip>${esc(row.model_name)}</span></button>`:'—';}
  function renderLogDetail(row){
    const field=(label,value,copyField='',privateValue=false)=>`<div><dt>${esc(t(label))}</dt><dd><span data-i18n-skip>${privateValue?masked(value):esc(value || '—')}</span>${copyField&&value&&(!privateValue||sensitiveVisible)?`<button type="button" class="api-icon-button" data-log-copy-field="${copyField}" data-log-copy-row="${esc(row.id)}" aria-label="${esc(t('复制')+' '+t(label))}" title="${esc(t('复制')+' '+t(label))}">${icon('copy')}</button>`:''}</dd></div>`;
    const section=(title,body)=>`<section class="api-log-detail-section"><h4>${esc(t(title))}</h4><dl>${body}</dl></section>`;
    let requestFields=field('请求 ID',row.request_id,'request_id',true)+field('上游请求 ID',row.upstream_request_id,'upstream_request_id',true)+field('令牌',row.token_name,'',true)+field('分组',row.group,'',true)+field('时间',time(row.created_at));
    if([2,5].includes(row.type))requestFields+=field('流',t(row.is_stream?'流式':'非流'))+field('耗时',duration(row.use_time))+(row.is_stream?field('首字',row.billing?.frt>0?duration(row.billing.frt/1000):'—'):'');
    let tokenFields=field('输入 Token',num(row.prompt_tokens))+field('输出 Token',num(row.completion_tokens));
    for(const [key,label] of Object.entries({cache_tokens:'缓存读取',cache_creation_tokens:'缓存写入'}))if(typeof row.billing?.[key]==='number')tokenFields+=field(label,num(row.billing[key]));
    let billingFields='';
    for(const [key,label] of Object.entries({model_ratio:'模型倍率',completion_ratio:'输出倍率',group_ratio:'分组倍率',user_group_ratio:'用户分组倍率',cache_ratio:'缓存倍率',cache_creation_ratio:'缓存写入倍率'}))if(typeof row.billing?.[key]==='number')billingFields+=field(label,row.billing[key].toLocaleString(document.documentElement.lang==='en'?'en-US':'zh-CN',{maximumFractionDigits:8})+'×');
    if(row.billing?.model_price>0)billingFields+=field('按次价格参数',num(row.billing.model_price));
    billingFields+=field('总费用',money(row.quota,logs.data.display));
    $('api-log-detail-type').textContent=t(types[row.type] || '未知');$('api-log-detail-type').className='api-log-type '+(row.type===5?'is-error':row.type===2?'is-consumption':'');
    $('api-log-detail').innerHTML=`<div class="api-log-detail-summary">${modelBadge(row)}<strong>${esc(money(row.quota,logs.data.display))}</strong></div>${section('请求信息',requestFields)}${[2,5].includes(row.type)?section('Token 明细',tokenFields):''}${section('计费详情',billingFields)}`;
  }
  async function copyLogValue(button,value,feedback){
    try{await navigator.clipboard.writeText(value);if(!$('view-api2api').isConnected || window.location.hash!=='#view-api2api')return;message(feedback,t('已复制'),true);const use=button.querySelector('use'),previous=use?.getAttribute('href');button.classList.add('is-copied');use?.setAttribute('href','/static/test-api-icons.svg#check');setTimeout(()=>{if(button.isConnected){button.classList.remove('is-copied');if(previous)use.setAttribute('href',previous);}},1600);}
    catch{message(feedback,t('请选中内容手动复制'));}
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
    message(feedback,t('加载中…'),false,'loading');
    const submit=$(kind==='logs'?'api-log-submit':'api-audit-submit');
    submit.disabled=true;
    if(kind==='logs')setLogBusy(true);
    try{
      const data=await request(kind==='audit'?`/api/admin/api2api/audit?${query(audit)}`:`/api/api2api/logs?${query(logs)}`,{method:'GET',signal:state.abort.signal});
      if(seq!==state.seq || !$('view-api2api').isConnected)return;
      const last=Math.max(1,Math.ceil(data.total/state.size));
      if(state.page>last){state.page=last;return load(kind);}
      state.data=data;message(feedback,'');
      if(kind==='logs')renderLogs();else if(kind==='audit')renderAudit();
    }catch(error){if(seq===state.seq && error.name!=='AbortError'){state.data=null;message(feedback,t(error.message));if(kind==='logs'){$('api-usage-rows').innerHTML=`<tr><td colspan="${visibleColumns.size}">${emptyLogs(true)}</td></tr>`;$('api-log-cards').innerHTML=emptyLogs(true);$('api-log-pager').innerHTML='';for(const id of ['api-log-quota','api-log-rpm','api-log-tpm'])$(id).textContent='—';$('api-log-stats-note').textContent='';if($('api-log-detail-dialog').open)$('api-log-detail-dialog').close();}else if(kind==='audit'){$('api-audit-rows').innerHTML='';$('api-audit-pager').innerHTML='';}}}
    finally{if(seq===state.seq){state.loading=false;submit.disabled=false;if(kind==='logs')setLogBusy(false);}}
  }
  function setLogBusy(busy){
    for(const id of ['api-log-table','api-log-cards'])$(id).setAttribute('aria-busy',String(busy));
    $('api-log-submit').textContent=t(busy?'搜索中…':'搜索');
    $('api-log-mobile-search').disabled=busy;
  }
  function setAdvanced(open){
    advancedOpen=open;$('api-log-extra').hidden=!open && !$('api-log-filter-dialog').open;
    const button=$('api-log-more');button.setAttribute('aria-expanded',String(open));button.querySelector('span').textContent=t(open?'收起':'展开');
    button.title=t(open?'收起更多筛选':'展开更多筛选');
  }
  function submitLogs(){
    preferenceTouched=true;
    let params;
    try{params=logParams();}catch(error){message('api-log-date-error',error.message);$('api-log-dates').open=true;if($('api-log-filter-dialog').open)$('api-log-filter-dialog').close();$('api-log-start').focus();return false;}
    savePreferences();
    logs.params=params;logs.page=1;dateLabel();message('api-log-date-error','');$('api-log-dates').open=false;
    if($('api-log-filter-dialog').open)$('api-log-filter-dialog').close();
    updateLogQueryNote();load('logs');return true;
  }
  function resetLogs(){
    $('api-log-form').reset();dates('today');setAdvanced(false);submitLogs();
  }
  $('api-log-filter-fields').querySelectorAll('input,select').forEach(node=>{node.setAttribute('form','api-log-form');for(const type of ['input','change'])node.addEventListener(type,()=>{preferenceTouched=true;updateLogQueryNote();});});
  $('api-log-open-filters').addEventListener('click',()=>{
    $('api-log-extra').hidden=false;$('api-log-filter-holder').append($('api-log-filter-fields'));
    $('api-log-filter-dialog').showModal();$('api-log-search').focus();
  });
  $('api-log-filter-dialog').addEventListener('close',()=>{
    $('api-log-filter-home').append($('api-log-filter-fields'));setAdvanced(advancedOpen);
    if(window.location.hash==='#view-api2api' && mobile?.matches!==false && !$('api-log-dates').open)$('api-log-open-filters').focus();
  });
  mobile?.addEventListener('change',()=>{if(!mobile.matches && $('api-log-filter-dialog').open){$('api-log-filter-dialog').close();$('api-log-search').focus();}});
  $('api-log-mobile-search').addEventListener('click',submitLogs);
  $('api-log-mobile-reset').addEventListener('click',resetLogs);
  $('api-log-privacy').addEventListener('click',()=>{sensitiveVisible=!sensitiveVisible;savePreferences();updatePrivacy();renderLogs();});
  function updatePrivacy(){
    const button=$('api-log-privacy'),label=t(sensitiveVisible?'隐藏令牌、分组和请求标识':'显示令牌、分组和请求标识');
    button.title=label;button.setAttribute('aria-label',label);button.setAttribute('aria-pressed',String(!sensitiveVisible));button.querySelector('use').setAttribute('href','/static/test-api-icons.svg#'+(sensitiveVisible?'eye-off':'eye'));
  }
  $('api-log-detail-dialog').addEventListener('close',()=>{
    detailId=null;$('api-log-detail').innerHTML='';$('api-log-detail-type').textContent='';message('api-log-copy-feedback','');
    if(window.location.hash==='#view-api2api' && detailOpener?.isConnected){detailOpener.focus({preventScroll:true});window.scrollTo({top:detailScroll,behavior:'instant'});}detailOpener=null;
  });
  async function activate(kind){active=kind;const epoch=++activationEpoch;await restorePreferences();if(active!==kind || epoch!==activationEpoch)return;if(kind==='logs' && !logs.params)logs.params=logParams();const state={logs,audit}[kind];if(kind==='models' || (state && !state.loading))return load(kind);}
  $('api-log-form').addEventListener('submit',event=>{event.preventDefault();submitLogs();});
  $('api-log-more').addEventListener('click',()=>setAdvanced(!advancedOpen));setAdvanced(false);
  document.querySelectorAll('[data-log-range]').forEach(button=>button.addEventListener('click',()=>{dates(button.dataset.logRange);submitLogs();}));
  for(const id of ['api-log-start','api-log-end'])$(id).addEventListener('input',()=>{savedRange='custom';preferenceTouched=true;document.querySelectorAll('[data-log-range]').forEach(node=>node.setAttribute('aria-pressed','false'));message('api-log-date-error','');updateLogQueryNote();});
  $('api-log-apply-dates').addEventListener('click',submitLogs);
  $('api-log-reset').addEventListener('click',resetLogs);
  $('api-audit-form').addEventListener('submit',event=>{event.preventDefault();audit.page=1;audit.params={q:$('api-audit-search').value.trim(),action:$('api-audit-action').value,outcome:$('api-audit-outcome').value,start:$('api-audit-start').value?new Date($('api-audit-start').value).toISOString():'',end:$('api-audit-end').value?new Date($('api-audit-end').value).toISOString():''};load('audit');});
  $('api-audit-reset').addEventListener('click',()=>{$('api-audit-form').reset();audit.params={};audit.page=1;load('audit');});
  $('api-log-column-options').innerHTML=Object.entries(columns).map(([key,label])=>`<label><input type="checkbox" data-log-column="${key}" checked ${key==='time'?'disabled':''} /><span ${key==='tokens'?'data-i18n-skip':''}>${esc(key==='tokens'?label:t(label))}</span></label>`).join('');
  $('api-log-column-options').addEventListener('change',event=>{const key=event.target.dataset.logColumn;if(!key)return;if(event.target.checked)visibleColumns.add(key);else visibleColumns.delete(key);savePreferences();renderLogs();});
  $('view-api2api').addEventListener('change',event=>{const kind=event.target.dataset.recordSize,state={logs,audit}[kind];if(state){state.size=Number(event.target.value);state.page=1;if(kind==='logs')savePreferences();else{auditPreferenceTouched=true;window.ViewPreferences?.save('api-audit',{size:state.size});}load(kind);}});
  $('view-api2api').addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button || button.disabled)return;
    const kind=button.dataset.recordKind,state={logs,audit}[kind];
    if(state){state.page=Number(button.dataset.recordPage);load(kind);return;}
    if(button.dataset.modelCopy){copyLogValue(button,button.dataset.modelCopy,$('api-log-detail-dialog').open?'api-log-copy-feedback':'api-log-feedback');return;}
    if(button.dataset.logCopyField){
      const field=button.dataset.logCopyField,row=logs.data?.items.find(row=>String(row.id)===button.dataset.logCopyRow);
      if(sensitiveVisible && row && ['request_id','upstream_request_id'].includes(field) && row[field])copyLogValue(button,row[field],'api-log-copy-feedback');return;
    }
    if(button.dataset.logDetail){
      const row=logs.data?.items.find(row=>String(row.id)===button.dataset.logDetail);if(!row)return;
      detailId=String(row.id);detailOpener=button;detailScroll=window.scrollY;renderLogDetail(row);message('api-log-copy-feedback','');$('api-log-detail-dialog').showModal();
    }
    if(button.dataset.logExport){
      if(exportAbort)return;const format=button.dataset.logExport,abort=new AbortController();exportAbort=abort;
      document.querySelectorAll('[data-log-export]').forEach(node=>{node.disabled=true;});$('api-log-export-menu').open=false;
      message('api-log-feedback',t('正在导出全部匹配日志…'),false,'loading');
      try{const response=await fetch(`/api/api2api/logs/export?${new URLSearchParams({...logs.params,format})}`,{cache:'no-store',signal:abort.signal});if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.detail || `HTTP ${response.status}`);}const blob=await response.blob();if(abort.signal.aborted || exportAbort!==abort || !$('view-api2api').isConnected || window.location.hash!=='#view-api2api')return;const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='api-usage-logs.'+format;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);message('api-log-feedback',`${response.headers.get('X-Export-Count') || ''} ${t('条日志已导出')}`,true);}
      catch(error){if(exportAbort===abort && !abort.signal.aborted && error.name!=='AbortError')message('api-log-feedback',t(error.message));}
      finally{if(exportAbort===abort){exportAbort=null;document.querySelectorAll('[data-log-export]').forEach(node=>{node.disabled=false;});}}
    }
  });
  document.addEventListener('click',event=>{document.querySelectorAll('.api-inline-menu[open],.api-date-picker[open]').forEach(node=>{if(!node.contains(event.target))node.open=false;});});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')document.querySelectorAll('.api-inline-menu[open],.api-date-picker[open]').forEach(node=>{node.open=false;});});
  function cancel(){
    activationEpoch++;
    for(const state of [logs,audit]){state.abort?.abort();state.seq++;state.loading=false;}
    $('api-log-submit').disabled=false;$('api-audit-submit').disabled=false;setLogBusy(false);
    exportAbort?.abort();exportAbort=null;document.querySelectorAll('[data-log-export]').forEach(node=>{node.disabled=false;});
    for(const id of ['api-log-filter-dialog','api-log-detail-dialog'])if($(id).open)$(id).close();
    for(const id of ['api-log-dates','api-log-export-menu','api-log-column-menu'])$(id).open=false;
    apiModelCatalog.cancel();
  }
  window.addEventListener('pagehide',cancel);window.addEventListener('hashchange',()=>{if(window.location.hash!=='#view-api2api')cancel();});
  document.addEventListener('site:language',()=>{dateLabel();updatePrivacy();setAdvanced(advancedOpen);setLogBusy(Boolean(logs.loading));renderLogs();renderAudit();});
  dates('today');
  return {start(value){admin=value;$('api-audit-tab').hidden=!value;if(!value){audit.abort?.abort();audit.seq++;audit.data=null;audit.loading=false;$('api-audit-rows').innerHTML='';$('api-audit-pager').innerHTML='';}},activate,async refresh(){await restorePreferences();if(active==='logs' && !logs.params)logs.params=logParams();if({logs,audit}[active]?.loading)return;return load(active);},setKeys(rows){ownKeys=rows;selectOptions('api-log-key',ownKeys.filter(row=>row.provider==='newapi' && row.available_here!==false).map(row=>[row.id,`${row.name} · ${row.key_prefix}…`]),'全部令牌');}};
})();
