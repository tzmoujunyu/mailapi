const apiModelCatalog = (() => {
  const $ = id => document.getElementById(id);
  const t = value => window.SiteLanguage?.t(value) || value;
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const numeric = value => typeof value==='number' && Number.isFinite(value);
  const num = (value,digits=4) => numeric(value)?value.toLocaleString(document.documentElement.lang==='en'?'en-US':'zh-CN',{maximumFractionDigits:digits}):'—';
  const latency = value => numeric(value) && value>0?(value<1000?num(value,0)+'ms':num(value/1000,2)+'s'):'—';
  const throughput = value => numeric(value) && value>0?(value>=1000?num(value/1000,1)+'K':num(value,1))+' t/s':'—';
  const icon = name => `<svg aria-hidden="true" viewBox="0 0 24 24"><use href="/static/test-api-icons.svg#${name}"></use></svg>`;
  const kindNames = {token:'按量计费',request:'按次计费',dynamic:'动态计费',unknown:'定价未提供'};
  const endpointNames = {openai:'Chat','openai-response':'Responses',anthropic:'Messages',embeddings:'Embeddings','image-generation':'Images','jina-rerank':'Rerank'};
  const priceNames = {input:'输入',output:'输出',cache_read:'缓存读取',cache_write:'缓存写入',request:'每次请求',image:'图像输入',audio_input:'音频输入'};
  const state = {data:null,page:1,size:20,view:'cards',unit:'M',price:'standard',filters:{group:'',vendor:'',tag:'',billing:'',endpoint:''},seq:0,loading:false};
  let selected=null,codeLanguage='curl',detailSeq=0,detailAbort=null,detailData=null;
  const items = () => state.data?.items || [];
  const groupNames = row => (row.groups || []).map(group=>group.id);
  const endpointLabel = row => endpointNames[row.id] || row.id;
  function feedback(value,detail=false){$(detail?'api-model-detail-feedback':'api-model-feedback').textContent=value;}
  function ratio(row,group=state.filters.group){
    const values=(row.groups || []).filter(value=>!group || value.id===group).map(value=>value.ratio).filter(numeric);
    return values.length?Math.min(...values):null;
  }
  function price(row,field,group=state.filters.group){
    const value=row.prices?.[field],multiple=ratio(row,group);
    if(!numeric(value) || multiple===null)return null;
    return value*multiple;
  }
  function amount(value,request=false){
    if(!numeric(value))return '—';
    const currency=state.data?.currency || {symbol:'$',standard_rate:1};
    const rate=currency[state.price==='recharge'?'recharge_rate':'standard_rate'];
    if(!numeric(rate))return '—';
    const converted=value*rate/(request || state.unit==='M'?1:1000);
    if(!numeric(converted))return '—';
    return currency.symbol+(converted>0 && converted<1e-8?converted.toExponential(2):num(converted,8));
  }
  const unit = row => row.billing==='request'?t('/ 次'):`/ 1${state.unit}`;
  function filtered(){
    const search=$('api-model-search').value.trim().toLowerCase(),f=state.filters;
    const result=items().filter(row=>(!search || [row.id,row.vendor,row.description,...(row.tags || []),...(row.endpoints || []).map(endpointLabel)].join(' ').toLowerCase().includes(search)) &&
      (!f.group || groupNames(row).includes(f.group)) && (!f.vendor || (row.vendor || '__unknown')===f.vendor) && (!f.tag || (row.tags || []).includes(f.tag)) &&
      (!f.billing || (row.billing || 'unknown')===f.billing) && (!f.endpoint || (row.endpoints || []).some(value=>value.id===f.endpoint)));
    const [field,direction]=$('api-model-sort').value.split('-');
    return result.sort((a,b)=>{
      if(field!=='name'){
        const av=price(a,field),bv=price(b,field);
        if(av===null && bv!==null)return 1;
        if(bv===null && av!==null)return -1;
        if(av!==null && bv!==null && av!==bv)return direction==='high'?bv-av:av-bv;
      }
      return a.id.localeCompare(b.id,undefined,{numeric:true,sensitivity:'base'});
    });
  }
  function filterOptions(){
    const unique = values => [...new Set(values)].sort((a,b)=>a.localeCompare(b));
    const rows=items();
    return [
      ['group','分组','所有分组',(state.data?.groups || []).map(group=>[group.id,group.id,numeric(group.ratio)?'×'+num(group.ratio):''])],
      ['vendor','供应商','所有供应商',unique(rows.map(row=>row.vendor || '__unknown')).map(value=>[value,value==='__unknown'?t('供应商未提供'):value,''])],
      ['tag','模型标签','所有标签',unique(rows.flatMap(row=>row.tags || [])).map(value=>[value,value,''])],
      ['billing','定价类型','所有模型',unique(rows.map(row=>row.billing || 'unknown')).map(value=>[value,t(kindNames[value]),''])],
      ['endpoint','端点类型','所有类型',unique(rows.flatMap(row=>(row.endpoints || []).map(value=>value.id))).map(value=>[value,endpointNames[value] || value,''])],
    ];
  }
  function count(kind,value){
    return items().filter(row=>kind==='group'?groupNames(row).includes(value):kind==='vendor'?(row.vendor || '__unknown')===value:kind==='tag'?(row.tags || []).includes(value):kind==='billing'?(row.billing || 'unknown')===value:(row.endpoints || []).some(endpoint=>endpoint.id===value)).length;
  }
  function renderFilters(){
    const focused=document.activeElement?.dataset,focusKey=focused?.catalogFilter,focusValue=focused?.value;
    const opened=new Map([...$('api-model-facets').querySelectorAll('details')].map(node=>[node.dataset.facet,node.open]));
    $('api-model-facets').innerHTML=filterOptions().map(([key,label,all,options])=>`<details data-facet="${key}" ${opened.get(key)!==false?'open':''}><summary>${esc(t(label))}</summary><div class="api-catalog-chips"><button type="button" data-catalog-filter="${key}" data-value="" aria-pressed="${!state.filters[key]}">${esc(t(all))}<small>${num(items().length)}</small></button>${options.map(([value,name,suffix])=>`<button type="button" data-catalog-filter="${key}" data-value="${esc(value)}" aria-pressed="${state.filters[key]===value}"><span data-i18n-skip>${esc(name)}</span><small>${esc(suffix || num(count(key,value)))}</small></button>`).join('')}</div></details>`).join('');
    $('api-model-reset').disabled=!$('api-model-search').value && !Object.values(state.filters).some(Boolean);
    if(focusKey)for(const button of $('api-model-facets').querySelectorAll('[data-catalog-filter]'))if(button.dataset.catalogFilter===focusKey && button.dataset.value===focusValue)button.focus({preventScroll:true});
  }
  function badges(values){return values.length?values.map(value=>`<span class="api-catalog-badge" data-i18n-skip>${esc(value)}</span>`).join(''):'—';}
  function brand(row){return `<span class="api-model-brand" aria-hidden="true">${esc((row.vendor || 'M').slice(0,2).toUpperCase())}</span>`;}
  function prices(row){
    const fields=row.billing==='request'?['request']:['input','output',...(numeric(row.prices?.cache_read)?['cache_read']:[])];
    if(row.billing==='dynamic' || !Object.keys(row.prices || {}).length)return `<p class="api-catalog-price-empty">${esc(t(row.billing==='dynamic'?'按实际用量与请求条件计费':'价格信息暂未提供'))}</p>`;
    return `<dl class="api-catalog-prices">${fields.map(field=>`<div><dt>${esc(t(priceNames[field]))}</dt><dd>${esc(amount(price(row,field),row.billing==='request'))}<small data-i18n-skip>${esc(unit(row))}</small></dd></div>`).join('')}</dl>`;
  }
  function uptime(row){
    const points=(row.recent || []).filter(value=>numeric(value.success_rate));
    return points.length?`<span class="api-model-uptime" aria-label="${esc(t('有样本时段的成功率'))}">${points.map(point=>`<i style="--uptime-color:${point.success_rate>=99?'#20b486':point.success_rate>=90?'#e7ae41':'#e96b79'}" title="${esc(new Date(point.ts*1000).toLocaleString())}: ${num(point.success_rate,2)}%"></i>`).join('')}</span>`:'';
  }
  function render(){
    if(!state.data)return;
    if(state.price==='recharge' && !numeric(state.data.currency?.recharge_rate))state.price='standard';
    renderFilters();
    const rows=filtered(),pages=Math.max(1,Math.ceil(rows.length/state.size));state.page=Math.min(state.page,pages);
    const visible=rows.slice((state.page-1)*state.size,state.page*state.size);
    $('api-model-total').textContent=`${t('当前账户可用模型')}: ${num(items().length)}`;
    $('api-model-count').textContent=`${num(rows.length)} ${t('个模型')}`;
    $('api-model-price-note').textContent=t(state.filters.group?'显示所选分组价格；最终费用以使用日志为准。':'全部分组按最低已知分组价格展示；最终费用以使用日志为准。');
    $('api-model-recharge').disabled=!numeric(state.data.currency?.recharge_rate);
    for(const [name,value] of [['price',state.price],['unit',state.unit],['view',state.view]])document.querySelectorAll(`[data-catalog-${name}]`).forEach(node=>node.setAttribute('aria-pressed',String(node.dataset['catalog'+name[0].toUpperCase()+name.slice(1)]===value)));
    $('api-model-grid').hidden=state.view!=='cards';$('api-model-table-wrap').hidden=state.view!=='table';
    $('api-model-grid').innerHTML=visible.map(row=>`<article class="api-model-tile"><header>${brand(row)}<div><h4><button type="button" class="api-link" data-catalog-detail="${esc(row.id)}" data-i18n-skip>${esc(row.id)}</button></h4><p data-i18n-skip>${esc(row.vendor || t('供应商未提供'))}</p></div><button type="button" class="api-icon-button" data-catalog-copy="${esc(row.id)}" title="${esc(t('复制模型 ID'))}" aria-label="${esc(t('复制模型 ID')+' '+row.id)}">${icon('copy')}</button></header><p class="api-model-description" data-i18n-skip title="${esc(row.description || '')}">${esc(row.description || t('暂无描述。'))}</p><span class="api-catalog-kind">${esc(t(kindNames[row.billing] || '定价未提供'))}</span>${prices(row)}<dl class="api-catalog-meta"><div><dt>${esc(t('分组'))}</dt><dd title="${esc(groupNames(row).join(', '))}" data-i18n-skip>${esc(groupNames(row)[0] || '—')}${groupNames(row).length>1?` <small>+${groupNames(row).length-1}</small>`:''}</dd></div><div><dt>${esc(t('端点'))}</dt><dd title="${esc((row.endpoints || []).map(endpointLabel).join(', '))}">${esc((row.endpoints || []).map(endpointLabel).join(', ') || '—')}</dd></div></dl><footer title="${esc(t('站点近 24 小时汇总；测试环境数据仅供验证。'))}"><div><small>${esc(t('成功率'))}</small><b>${numeric(row.performance?.success_rate)?num(row.performance.success_rate,2)+'%':'—'}</b>${uptime(row)}</div><div><small>${esc(t('延迟'))}</small><b>${latency(row.performance?.avg_latency_ms)}</b></div><div><small>${esc(t('吞吐'))}</small><b>${throughput(row.performance?.avg_tps)}</b></div><button type="button" class="api-link" data-catalog-detail="${esc(row.id)}">${esc(t('详情'))} →</button></footer></article>`).join('') || `<div class="api-empty">${esc(t('暂无符合条件的可用模型。'))}</div>`;
    $('api-model-rows').innerHTML=visible.map(row=>`<tr><td><button class="api-link api-model-table-name" type="button" data-catalog-detail="${esc(row.id)}" data-i18n-skip>${brand(row)}${esc(row.id)}</button></td><td>${esc(t(kindNames[row.billing] || '定价未提供'))}</td><td>${prices(row)}</td><td class="api-number">${esc(amount(price(row,'cache_read')))}</td><td data-i18n-skip>${esc(row.vendor || '—')}</td><td>${badges(row.tags || [])}</td><td>${badges((row.endpoints || []).map(endpointLabel))}</td><td>${badges(groupNames(row))}</td><td><button type="button" class="api-icon-button" data-catalog-copy="${esc(row.id)}" aria-label="${esc(t('复制模型 ID')+' '+row.id)}">${icon('copy')}</button><button type="button" class="api-link" data-catalog-detail="${esc(row.id)}">${esc(t('详情'))}</button></td></tr>`).join('') || `<tr><td colspan="9" class="api-empty">${esc(t('暂无符合条件的可用模型。'))}</td></tr>`;
    const pageButton=(page,label,content,disabled)=>`<button type="button" data-catalog-page="${page}" aria-label="${esc(t(label))}" ${disabled?'disabled':''}>${content}</button>`;
    const numbers=[];for(let page=Math.max(1,state.page-1);page<=Math.min(pages,state.page+1);page++)numbers.push(`<button type="button" data-catalog-page="${page}" ${page===state.page?'aria-current="page"':''}>${page}</button>`);
    $('api-model-pager').innerHTML=`<span>${esc(t('总计'))}: ${num(rows.length)}</span><div class="api-pager-controls"><label>${esc(t('每页行数'))}<select id="api-model-page-size">${[20,50,100].map(size=>`<option ${size===state.size?'selected':''}>${size}</option>`).join('')}</select></label>${pageButton(1,'首页','«',state.page===1)}${pageButton(state.page-1,'上一页','‹',state.page===1)}${numbers.join('')}${pageButton(state.page+1,'下一页','›',state.page===pages)}${pageButton(pages,'末页','»',state.page===pages)}</div>`;
    if(!state.data.pricing_available || !state.data.performance_available)feedback(t('部分价格或性能数据尚未提供，缺失项目显示为 —。'));
  }
  async function load(){
    state.abort?.abort();state.abort=new AbortController();const seq=++state.seq;state.loading=true;feedback(t('加载中…'));
    try{
      const response=await fetch('/api/api2api/models',{method:'POST',cache:'no-store',signal:state.abort.signal}),data=await response.json();
      if(!response.ok)throw new Error(data.detail || `HTTP ${response.status}`);
      if(seq!==state.seq || !$('view-api2api').isConnected)return;
      state.data=data;feedback('');render();
      if(selected && !items().some(row=>row.id===selected.id))$('api-model-dialog').close();
    }catch(error){if(seq===state.seq && error.name!=='AbortError'){state.data=null;feedback(t(error.message));for(const id of ['api-model-grid','api-model-rows','api-model-pager','api-model-facets','api-model-count','api-model-total','api-model-price-note'])$(id).textContent='';$('api-model-dialog').close();}}
    finally{if(seq===state.seq)state.loading=false;}
  }
  function overview(){
    if(!selected)return;
    const row=selected,fields=Object.keys(row.prices || {});
    const priceRow=(label,multiple)=>`<tr><th data-i18n-skip>${esc(label)}</th>${fields.map(field=>`<td class="api-number">${esc(amount(numeric(multiple)?row.prices[field]*multiple:null,row.billing==='request'))}</td>`).join('')}</tr>`;
    $('api-model-overview').innerHTML=`<p data-i18n-skip>${esc(row.description || t('暂无描述。'))}</p><dl class="api-model-detail-meta"><div><dt>${esc(t('供应商'))}</dt><dd data-i18n-skip>${esc(row.vendor || '—')}</dd></div><div><dt>${esc(t('计费类型'))}</dt><dd>${esc(t(kindNames[row.billing] || '定价未提供'))}</dd></div><div><dt>${esc(t('分组'))}</dt><dd>${badges(groupNames(row))}</dd></div><div><dt>${esc(t('模型标签'))}</dt><dd>${badges(row.tags || [])}</dd></div></dl><h4>${esc(t('定价'))}</h4>${fields.length?`<div class="api-table-wrap"><table aria-label="${esc(t('按分组定价'))}"><thead><tr><th>${esc(t('分组'))}</th>${fields.map(field=>`<th class="api-number">${esc(t(priceNames[field]))}</th>`).join('')}</tr></thead><tbody>${priceRow(t('基础价格'),1)}${(row.groups || []).map(group=>priceRow(group.id+(numeric(group.ratio)?' ×'+num(group.ratio):''),group.ratio)).join('')}</tbody></table></div>`:`<p class="api-hint">${esc(t(row.billing==='dynamic'?'动态计费按实际请求条件计算，具体扣费可在使用日志中查看。':'价格信息暂未提供'))}</p>`}<p class="api-hint">${esc(unit(row))}${row.billing==='request'?'':` ${esc(t('Token 单位'))}`} · ${esc(t('最终费用以使用日志为准。'))}</p><h4>${esc(t('端点'))}</h4>${badges((row.endpoints || []).map(endpointLabel))}<p class="api-hint">${esc(t('性能指标为站点近 24 小时汇总，不代表个人调用记录。'))}</p>`;
  }
  function chart(points,field,label,suffix){
    const data=(points || []).filter(row=>numeric(row.ts) && numeric(row[field]));
    if(!data.length)return `<p class="api-empty">${esc(t('暂无性能数据'))}</p>`;
    if(data.length===1)return `<div class="api-model-single-sample"><strong>${num(data[0][field],2)}${suffix}</strong><span data-i18n-skip>${esc(new Date(data[0].ts*1000).toLocaleString())}</span><small>${esc(t('仅一个时段有数据，累积后显示趋势。'))}</small></div>`;
    const first=data[0].ts,span=Math.max(1,data.at(-1).ts-first),maximum=Math.max(...data.map(row=>row[field])) || 1;
    const position=row=>[25+590*(row.ts-first)/span,110-90*row[field]/maximum];
    return `<svg viewBox="0 0 640 145" class="api-model-chart" role="img" aria-label="${esc(t(label))}"><line x1="25" y1="110" x2="615" y2="110"/><polyline points="${data.map(row=>position(row).join(',')).join(' ')}"/>${data.map(row=>`<circle cx="${position(row)[0]}" cy="${position(row)[1]}" r="3" tabindex="0"><title>${esc(new Date(row.ts*1000).toLocaleString())}: ${num(row[field],2)}${suffix}</title></circle>`).join('')}<text x="25" y="137">${esc(new Date(first*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}))}</text><text x="615" y="137" text-anchor="end">${esc(new Date(data.at(-1).ts*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}))}</text></svg>`;
  }
  function renderPerformance(){
    const data=detailData,summary=data?.summary;
    $('api-model-performance').innerHTML=`<div class="api-model-metrics"><div><span>${esc(t('成功率'))}</span><strong>${numeric(summary?.success_rate)?num(summary.success_rate,2)+'%':'—'}</strong></div><div><span>${esc(t('平均延迟'))}</span><strong>${latency(summary?.avg_latency_ms)}</strong></div><div><span>TPS</span><strong>${throughput(summary?.avg_tps)}</strong></div></div><p class="api-hint">${esc(t('性能指标为站点近 24 小时汇总，不代表个人调用记录。'))}</p><h4>${esc(t('各分组性能'))}</h4><div class="api-table-wrap"><table><thead><tr>${['分组','成功率','平均首 Token 延迟','平均延迟','吞吐'].map(value=>`<th>${esc(t(value))}</th>`).join('')}</tr></thead><tbody>${(data?.groups || []).map(group=>`<tr><td data-i18n-skip>${esc(group.id)}</td><td>${numeric(group.summary?.success_rate)?num(group.summary.success_rate,2)+'%':'—'}</td><td>${latency(group.summary?.avg_ttft_ms)}</td><td>${latency(group.summary?.avg_latency_ms)}</td><td>${throughput(group.summary?.avg_tps)}</td></tr>`).join('') || `<tr><td colspan="5" class="api-empty">${esc(t('暂无性能数据'))}</td></tr>`}</tbody></table></div><h4>${esc(t('延迟趋势'))}</h4>${chart((data?.series || []).map(row=>({...row,seconds:numeric(row.avg_latency_ms)?row.avg_latency_ms/1000:null})),'seconds','延迟趋势','s')}<h4>${esc(t('成功率趋势'))}</h4>${chart(data?.series,'success_rate','成功率趋势','%')}`;
  }
  async function performance(){
    if(!selected)return;detailAbort?.abort();detailAbort=new AbortController();const seq=++detailSeq,id=selected.id;feedback(t('加载中…'),true);
    try{
      const response=await fetch('/api/api2api/models/performance?'+new URLSearchParams({model:id}),{cache:'no-store',signal:detailAbort.signal}),data=await response.json();
      if(!response.ok)throw new Error(data.detail || `HTTP ${response.status}`);
      if(seq!==detailSeq || !$('api-model-dialog').open || !$('view-api2api').isConnected)return;
      detailData=data;feedback('',true);renderPerformance();
    }catch(error){if(seq===detailSeq && error.name!=='AbortError'){detailData=null;renderPerformance();feedback(t(error.message),true);}}
  }
  function example(){
    if(!selected)return;
    const endpoint=(selected.endpoints || []).find(value=>value.id===$('api-model-example-endpoint').value),host=location.origin+'/api2api';
    const path=endpoint?.path,payload=path==='/v1/chat/completions'?{model:selected.id,messages:[{role:'user',content:'Hello'}]}:path==='/v1/responses'?{model:selected.id,input:'Hello'}:path==='/v1/embeddings'?{model:selected.id,input:'Hello'}:null;
    $('api-model-copy-code').disabled=!payload;
    if(!payload){$('api-model-example').textContent=endpoint?`${endpoint.method} ${host}${path}\n\n${t('请按此端点协议准备请求体，并使用 Authorization: Bearer API_KEY。')}`:t('端点信息暂未提供');return;}
    const url=host+path,body=JSON.stringify(payload,null,2),quote=value=>"'"+value.replace(/'/g,"'\"'\"'")+"'";
    $('api-model-example').textContent=codeLanguage==='curl'?`curl ${quote(url)} \\\n  -H "Authorization: Bearer $API_KEY" \\\n  -H 'Content-Type: application/json' \\\n  -d ${quote(body)}`:codeLanguage==='python'?`import json, os, urllib.request\n\npayload = ${body}\nrequest = urllib.request.Request(\n    ${JSON.stringify(url)},\n    data=json.dumps(payload).encode(),\n    headers={"Authorization": "Bearer " + os.environ["API_KEY"],\n             "Content-Type": "application/json"},\n)\nwith urllib.request.urlopen(request) as response:\n    print(response.read().decode())`:`const response = await fetch(${JSON.stringify(url)}, {\n  method: 'POST',\n  headers: {\n    Authorization: 'Bearer ' + process.env.API_KEY,\n    'Content-Type': 'application/json',\n  },\n  body: JSON.stringify(${body}),\n});\n${codeLanguage==='typescript'?'const data: unknown = await response.json();\nconsole.log(data);':'console.log(await response.json());'}`;
  }
  function renderApi(){
    const endpoint=$('api-model-example-endpoint').value;
    $('api-model-example-endpoint').innerHTML=(selected.endpoints || []).map(value=>`<option value="${esc(value.id)}" data-i18n-skip>${esc(endpointLabel(value))}</option>`).join('');
    if((selected.endpoints || []).some(value=>value.id===endpoint))$('api-model-example-endpoint').value=endpoint;
    $('api-model-endpoint-list').innerHTML=`<h4>${esc(t('可用端点'))}</h4>`+(selected.endpoints || []).map(value=>`<div class="api-model-endpoint"><b data-i18n-skip>${esc(value.method)}</b><code data-i18n-skip>${esc(location.origin+'/api2api'+value.path)}</code></div>`).join('');
    example();
  }
  function selectDetailTab(value){
    if(value!=='performance'){detailAbort?.abort();detailSeq++;feedback('',true);}document.querySelectorAll('[data-catalog-detail-tab]').forEach(node=>node.setAttribute('aria-pressed',String(node.dataset.catalogDetailTab===value)));
    document.querySelectorAll('[data-catalog-detail-panel]').forEach(node=>{node.hidden=node.dataset.catalogDetailPanel!==value;});
    if(value==='performance' && !detailData)performance();
  }
  function open(id){
    selected=items().find(row=>row.id===id);if(!selected)return;
    detailAbort?.abort();detailSeq++;detailData=null;feedback('',true);
    $('api-model-dialog-title').textContent=selected.id;$('api-model-dialog-subtitle').textContent=(selected.vendor || t('供应商未提供'))+' · '+t(kindNames[selected.billing] || '定价未提供');
    overview();renderPerformance();renderApi();selectDetailTab('overview');$('api-model-dialog').showModal();
  }
  async function copy(value,detail=false){try{await navigator.clipboard.writeText(value);feedback(t('已复制'),detail);}catch{feedback(t('请选中内容手动复制'),detail);}}
  $('api-model-search').addEventListener('input',()=>{state.page=1;render();});
  $('api-model-sort').addEventListener('change',()=>{state.page=1;render();});
  $('api-model-reset').addEventListener('click',()=>{$('api-model-search').value='';for(const key in state.filters)state.filters[key]='';state.page=1;render();});
  $('api-model-filters-toggle').addEventListener('click',()=>{const opened=$('api-model-filters').classList.toggle('is-open');$('api-model-filters-toggle').setAttribute('aria-expanded',String(opened));});
  $('api-model-dialog-close').addEventListener('click',()=>$('api-model-dialog').close());
  $('api-model-dialog').addEventListener('close',()=>{detailAbort?.abort();detailSeq++;selected=null;detailData=null;});
  $('api-model-dialog-copy').addEventListener('click',()=>{if(selected)copy(selected.id,true);});
  $('api-model-copy-code').addEventListener('click',()=>copy($('api-model-example').textContent,true));
  $('api-model-example-endpoint').addEventListener('change',example);
  $('view-api2api').addEventListener('change',event=>{if(event.target.id==='api-model-page-size'){state.size=Number(event.target.value);state.page=1;render();}});
  $('view-api2api').addEventListener('click',event=>{
    const button=event.target.closest('button');if(!button || button.disabled)return;const d=button.dataset;
    if(d.catalogFilter){state.filters[d.catalogFilter]=d.value;state.page=1;render();}
    if(d.catalogPage){state.page=Number(d.catalogPage);render();}
    if(d.catalogPrice){state.price=d.catalogPrice;render();if(selected)overview();}
    if(d.catalogUnit){state.unit=d.catalogUnit;render();if(selected)overview();}
    if(d.catalogView){state.view=d.catalogView;render();}
    if(d.catalogDetail)open(d.catalogDetail);
    if(d.catalogCopy)copy(d.catalogCopy);
    if(d.catalogDetailTab)selectDetailTab(d.catalogDetailTab);
    if(d.catalogCode){codeLanguage=d.catalogCode;document.querySelectorAll('[data-catalog-code]').forEach(node=>node.setAttribute('aria-pressed',String(node===button)));example();}
  });
  function cancel(){state.abort?.abort();detailAbort?.abort();}
  document.addEventListener('site:language',()=>{render();if(selected){$('api-model-dialog-subtitle').textContent=(selected.vendor || t('供应商未提供'))+' · '+t(kindNames[selected.billing] || '定价未提供');overview();renderPerformance();renderApi();}});
  window.addEventListener('hashchange',()=>{if(location.hash!=='#view-api2api')cancel();});window.addEventListener('pagehide',cancel);
  return {load,cancel};
})();
