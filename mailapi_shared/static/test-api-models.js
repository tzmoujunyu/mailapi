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
  const providers = row => window.MilvlgBilling?.analyze(row) || [];
  const summary = row => {
    const dynamic=window.MilvlgBilling?.summarize(row);
    if(row.billing==='dynamic' && providers(row).length)return dynamic || [];
    return Object.entries(row.prices || {}).map(([key,value])=>({key,value,label:priceNames[key] || key,unit:key==='request'?'request':'token'}));
  };
  const entryValue = entry => entry.value;
  const entryUnit = entry => {
    const language=document.documentElement.lang || 'zh-CN',label=entry.unitLabel;
    const custom=typeof label==='string'?label:label?.[language] || label?.[language.split('-')[0]] || label?.en;
    return entry.unit==='token'?`/ 1${state.unit}`:entry.unit==='count' && custom?'/ '+custom:t(({request:'/ 次',image:'/ 张',second:'/ 秒',credit:'/ 积分'})[entry.unit] || '/ 单位');
  };
  const entryAmount = (entry,multiple=1,value=entry.value) => amount(numeric(multiple)?value*multiple:null,entry.unit!=='token');
  const entryLabel = entry => entry.schema?entry.label:t(entry.label);
  function feedback(value,detail=false){$(detail?'api-model-detail-feedback':'api-model-feedback').textContent=value;}
  function ratio(row,group=state.filters.group){
    const values=(row.groups || []).filter(value=>!group || value.id===group).map(value=>value.ratio).filter(numeric);
    return values.length?Math.min(...values):null;
  }
  function price(row,field,group=state.filters.group){
    const entry=summary(row).find(value=>value.key===field),value=entry?entryValue(entry):null,multiple=ratio(row,group);
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
    $('api-model-facets').innerHTML=filterOptions().map(([key,label,all,options])=>`<details data-facet="${key}" ${opened.get(key)!==false?'open':''}><summary><span>${esc(t(label))}</span>${icon('chevron-down')}</summary><div class="api-catalog-chips"><button type="button" data-catalog-filter="${key}" data-value="" aria-pressed="${!state.filters[key]}"><span>${esc(t(all))}</span><small>${num(items().length)}</small></button>${options.map(([value,name,suffix])=>`<button type="button" data-catalog-filter="${key}" data-value="${esc(value)}" aria-pressed="${state.filters[key]===value}"><span data-i18n-skip>${esc(name)}</span><small>${esc(suffix || num(count(key,value)))}</small></button>`).join('')}</div></details>`).join('');
    $('api-model-reset').disabled=!$('api-model-search').value && !Object.values(state.filters).some(Boolean);
    const active=filterOptions().flatMap(([key,label,,options])=>{
      const value=state.filters[key];
      return value?[[key,t(label),options.find(option=>option[0]===value)?.[1] || value]]:[];
    });
    if($('api-model-search').value)active.unshift(['search',t('搜索'),$('api-model-search').value]);
    $('api-model-active-filters').innerHTML=active.map(([key,label,value])=>`<button type="button" data-catalog-clear="${key}" aria-label="${esc(t('移除筛选')+' · '+label+': '+value)}"><span data-i18n-skip>${esc(label)}: ${esc(value)}</span>${icon('close')}</button>`).join('');
    $('api-model-active-filters').hidden=!active.length;
    $('api-model-filter-count').textContent=active.length || '';
    $('api-model-filter-count').hidden=!active.length;
    if(focusKey)for(const button of $('api-model-facets').querySelectorAll('[data-catalog-filter]'))if(button.dataset.catalogFilter===focusKey && button.dataset.value===focusValue)button.focus({preventScroll:true});
  }
  function badges(values){return values.length?values.map(value=>`<span class="api-catalog-badge" data-i18n-skip>${esc(value)}</span>`).join(''):'—';}
  function brand(row){return `<span class="api-model-brand" aria-hidden="true">${row.vendor?esc(row.vendor.trim().split(/\s+/).map(word=>Array.from(word)[0]).slice(0,2).join('').toUpperCase()):icon('model')}</span>`;}
  const kindBadge = row => `<span class="api-catalog-kind" data-billing-kind="${esc(row.billing || 'unknown')}">${esc(t(kindNames[row.billing] || '定价未提供'))}</span>`;
  function card(row){
    return `<article class="api-model-tile">
      <header>${brand(row)}<div><h4><button type="button" class="api-link" data-catalog-detail="${esc(row.id)}" data-i18n-skip>${esc(row.id)}</button></h4><p data-i18n-skip>${esc(row.vendor || t('供应商未提供'))}</p></div><button type="button" class="api-icon-button" data-catalog-copy="${esc(row.id)}" title="${esc(t('复制模型 ID'))}" aria-label="${esc(t('复制模型 ID')+' '+row.id)}">${icon('copy')}</button></header>
      <p class="api-model-description" data-i18n-skip title="${esc(row.description || '')}">${esc(row.description || t('暂无描述。'))}</p>
      <div class="api-model-pricing">${kindBadge(row)}${prices(row)}</div>
      <dl class="api-catalog-meta"><div><dt>${esc(t('分组'))}</dt><dd title="${esc(groupNames(row).join(', '))}">${badges(groupNames(row).slice(0,2))}${groupNames(row).length>2?`<span class="api-catalog-badge" data-i18n-skip>+${groupNames(row).length-2}</span>`:''}</dd></div><div><dt>${esc(t('端点'))}</dt><dd>${badges((row.endpoints || []).map(endpointLabel))}</dd></div></dl>
      <footer title="${esc(t('站点近 24 小时汇总；测试环境数据仅供验证。'))}"><div><small>${icon('activity')}${esc(t('成功率'))}</small><b>${numeric(row.performance?.success_rate)?num(row.performance.success_rate,2)+'%':'—'}</b>${uptime(row)}</div><div><small>${icon('clock')}${esc(t('延迟'))}</small><b>${latency(row.performance?.avg_latency_ms)}</b></div><div><small>${icon('gauge')}${esc(t('吞吐'))}</small><b>${throughput(row.performance?.avg_tps)}</b></div><button type="button" class="api-link api-model-details-link" data-catalog-detail="${esc(row.id)}">${esc(t('详情'))}${icon('chevron-right')}</button></footer>
    </article>`;
  }
  function prices(row){
    const entries=summary(row).slice(0,3);
    if(!entries.length)return `<p class="api-catalog-price-empty">${esc(t(row.billing==='dynamic'?'查看完整计费规则':'价格信息暂未提供'))}</p>`;
    const dynamic=providers(row),count=dynamic.reduce((sum,value)=>sum+value.tiers.length,0);
    return `<dl class="api-catalog-prices" style="--api-price-columns:${entries.length}">${entries.map(entry=>`<div data-price-key="${esc(entry.key)}"><dt data-i18n-skip>${esc(entryLabel(entry))}</dt><dd><span>${esc(entryAmount(entry,ratio(row)))}${numeric(entry.max) && entry.max!==entry.value?' – '+esc(entryAmount(entry,ratio(row),entry.max)):''}</span><small data-i18n-skip>${esc(entryUnit(entry))}</small></dd></div>`).join('')}</dl>${row.billing==='dynamic'?`<p class="api-catalog-tier-note">${esc(t('分档定价'))} · ${num(count)} ${esc(t('档位'))}${dynamic.some(value=>value.rules.length)?' · '+esc(t('请求附加倍率')):''}</p>`:''}`;
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
    $('api-model-grid').innerHTML=visible.map(card).join('') || `<div class="api-model-empty">${icon('model')}<p>${esc(t('暂无符合条件的可用模型。'))}</p></div>`;
    $('api-model-rows').innerHTML=visible.map(row=>`<tr><td><button class="api-link api-model-table-name" type="button" data-catalog-detail="${esc(row.id)}" data-i18n-skip>${brand(row)}${esc(row.id)}</button></td><td>${kindBadge(row)}</td><td>${prices(row)}</td><td class="api-number">${esc(amount(price(row,'cache_read')))}</td><td data-i18n-skip>${esc(row.vendor || '—')}</td><td>${badges(row.tags || [])}</td><td>${badges((row.endpoints || []).map(endpointLabel))}</td><td>${badges(groupNames(row))}</td><td><button type="button" class="api-icon-button" data-catalog-copy="${esc(row.id)}" aria-label="${esc(t('复制模型 ID')+' '+row.id)}">${icon('copy')}</button><button type="button" class="api-link" data-catalog-detail="${esc(row.id)}">${esc(t('详情'))}</button></td></tr>`).join('') || `<tr><td colspan="9" class="api-empty">${esc(t('暂无符合条件的可用模型。'))}</td></tr>`;
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
    }catch(error){if(seq===state.seq && error.name!=='AbortError'){state.data=null;feedback(t(error.message));for(const id of ['api-model-grid','api-model-rows','api-model-pager','api-model-facets','api-model-count','api-model-total','api-model-price-note','api-model-active-filters'])$(id).textContent='';$('api-model-active-filters').hidden=true;$('api-model-filter-count').hidden=true;$('api-model-dialog').close();}}
    finally{if(seq===state.seq)state.loading=false;}
  }
  function tierTable(provider,multiple,label){
    const columns=[...new Map(provider.tiers.flatMap(tier=>tier.entries).map(entry=>[entry.key,entry])).values()];
    if(!provider.tiers.length)return `<p class="api-hint">${esc(t('此表达式不能拆为固定单价，请查看完整计费规则。'))}</p>`;
    return `<div class="api-table-wrap api-tier-table-wrap"><table class="api-tier-table" aria-label="${esc(label)}"><thead><tr><th>${esc(t('档位与适用条件'))}</th>${columns.map(entry=>`<th><span data-i18n-skip>${esc(entryLabel(entry))}</span><small data-i18n-skip>${esc(entryUnit(entry))}</small></th>`).join('')}</tr></thead><tbody>${provider.tiers.map((tier,index)=>{
      const condition=window.MilvlgBilling.tierCondition(tier,provider.schema,document.documentElement.lang || 'zh-CN') || t(index?'其他情况（前序条件均不满足）':'全部请求');
      return `<tr><th><span class="api-tier-label" data-i18n-skip>${esc(tier.label || t('默认'))}</span><small class="api-tier-condition" data-i18n-skip>${esc(condition)}</small></th>${columns.map(column=>{const entry=tier.entries.find(value=>value.key===column.key);return `<td data-label="${esc(entryLabel(column)+' '+entryUnit(column))}" class="api-number">${entry?esc(entryAmount(entry,multiple)):'—'}</td>`;}).join('')}</tr>`;
    }).join('')}</tbody></table></div>`;
  }
  function priceSummary(entries,multiple=1){
    return `<dl class="api-detail-prices">${entries.map(entry=>`<div class="${['input','output','request'].includes(entry.key)?'is-primary':''}"><dt data-i18n-skip>${esc(entryLabel(entry))}</dt><dd>${esc(entryAmount(entry,multiple))}${numeric(entry.max) && entry.max!==entry.value?' – '+esc(entryAmount(entry,multiple,entry.max)):''}<small data-i18n-skip>${esc(entryUnit(entry))}</small></dd></div>`).join('')}</dl>`;
  }
  function dynamicPricing(row){
    const data=providers(row);
    if(!data.length)return `<p class="api-hint">${esc(t('价格信息暂未提供'))}</p>`;
    return data.map(provider=>`<section class="api-pricing-provider">${provider.label?`<h4 data-i18n-skip>${esc(provider.label==='常规调用'?t(provider.label):provider.label)}</h4>`:''}<h5>${esc(t('基础价格'))}</h5>${priceSummary(provider.summary)}<div class="api-dynamic-heading"><span class="api-catalog-kind" data-billing-kind="dynamic">${esc(t('动态计费'))}</span><p>${esc(t('价格根据用量档位和请求条件动态调整'))}</p></div><h5>${esc(t('分档价格表'))}</h5><p class="api-hint">${esc(t('按从上到下的顺序匹配档位；表中为基础倍率 1 的价格。'))}</p>${tierTable(provider,1,t('分档价格表'))}${provider.tiers.some(tier=>tier.imageCount)?`<p class="api-hint">${esc(t('图像请求的费用还会乘以图像数量。'))}</p>`:''}${provider.rules.length?`<h5>${esc(t('请求附加倍率'))}</h5><ul class="api-pricing-rules">${provider.rules.map(rule=>`<li><span data-i18n-skip>${esc(window.MilvlgBilling.condition(rule.condition,document.documentElement.lang || 'zh-CN'))}</span><b>×${num(rule.multiplier)}</b></li>`).join('')}</ul><p class="api-hint">${esc(t('仅实际命中的规则生效；未命中时倍率为 1，规则组合以完整表达式为准。'))}</p>`:''}${provider.examples.length?`<h5>${esc(t('用量示例'))}</h5><dl class="api-pricing-examples">${provider.examples.map(example=>`<div><dt data-i18n-skip>${esc(example.label)}</dt><dd>${esc(amount(example.total,true))}</dd></div>`).join('')}</dl>`:''}<h4>${esc(t('按分组定价'))}</h4>${(row.groups || []).map(group=>`<details class="api-pricing-group" open><summary><span data-i18n-skip>${esc(group.id)}</span><b>${numeric(group.ratio)?'×'+num(group.ratio):'—'}</b></summary>${tierTable(provider,group.ratio,t('按分组定价')+' '+group.id)}</details>`).join('')}<details class="api-pricing-expression" ${provider.special?'open':''}><summary>${esc(t('完整计费规则'))}</summary><pre data-i18n-skip>${esc(provider.expression || t('价格信息暂未提供'))}</pre></details></section>`).join('');
  }
  function overview(){
    if(!selected)return;
    const row=selected,fields=Object.keys(row.prices || {});
    const priceRow=(label,multiple)=>`<tr><th data-i18n-skip>${esc(label)}</th>${fields.map(field=>`<td class="api-number">${esc(amount(numeric(multiple)?row.prices[field]*multiple:null,row.billing==='request'))}</td>`).join('')}</tr>`;
    $('api-model-overview').innerHTML=`<div class="api-model-metrics api-overview-metrics"><div><span>TPS</span><strong>${throughput(row.performance?.avg_tps)}</strong></div><div><span>${esc(t('平均延迟'))}</span><strong>${latency(row.performance?.avg_latency_ms)}</strong></div><div><span>${esc(t('成功率'))}</span><strong>${numeric(row.performance?.success_rate)?num(row.performance.success_rate,2)+'%':'—'}</strong></div></div><section class="api-detail-section"><div class="api-pricing-title"><h4>${icon('tag')}${esc(t('定价'))}</h4><div class="api-segment" role="group" aria-label="${esc(t('Token 单位'))}">${['M','K'].map(value=>`<button type="button" data-catalog-unit="${value}" aria-pressed="${state.unit===value}" data-i18n-skip>/1${value}</button>`).join('')}</div></div>${row.billing==='dynamic'?dynamicPricing(row):`${priceSummary(summary(row))}<h4>${esc(t('按分组定价'))}</h4>${fields.length?`<div class="api-table-wrap"><table aria-label="${esc(t('按分组定价'))}"><thead><tr><th>${esc(t('分组'))}</th>${fields.map(field=>`<th class="api-number">${esc(t(priceNames[field]))}</th>`).join('')}</tr></thead><tbody>${priceRow(t('基础价格'),1)}${(row.groups || []).map(group=>priceRow(group.id+(numeric(group.ratio)?' ×'+num(group.ratio):''),group.ratio)).join('')}</tbody></table></div>`:`<p class="api-hint">${esc(t('价格信息暂未提供'))}</p>`}`}${row.auto_groups?.length?`<p class="api-auto-groups">${esc(t('自动分组链'))} → <span data-i18n-skip>${esc(row.auto_groups.join(' → '))}</span></p>`:''}<p class="api-hint">${esc(t('最终费用以使用日志为准。'))}</p></section><section class="api-detail-section"><h4>${esc(t('模型'))}</h4><p data-i18n-skip>${esc(row.description || t('暂无描述。'))}</p><dl class="api-model-detail-meta"><div><dt>${esc(t('供应商'))}</dt><dd data-i18n-skip>${esc(row.vendor || '—')}</dd></div><div><dt>${esc(t('计费类型'))}</dt><dd>${esc(t(kindNames[row.billing] || '定价未提供'))}</dd></div><div><dt>${esc(t('分组'))}</dt><dd>${badges(groupNames(row))}</dd></div><div><dt>${esc(t('端点'))}</dt><dd>${badges((row.endpoints || []).map(endpointLabel))}</dd></div>${row.tags?.length?`<div><dt>${esc(t('模型标签'))}</dt><dd>${badges(row.tags)}</dd></div>`:''}</dl></section>`;
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
    $('api-model-dialog-brand').innerHTML=brand(selected);$('api-model-dialog-title').textContent=selected.id;$('api-model-dialog-subtitle').textContent=(selected.vendor || t('供应商未提供'))+' · '+t(kindNames[selected.billing] || '定价未提供');
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
    if(d.catalogClear){
      if(d.catalogClear==='search')$('api-model-search').value='';else state.filters[d.catalogClear]='';
      state.page=1;render();
      ($('api-model-active-filters').querySelector('button') || $('api-model-search')).focus({preventScroll:true});
    }
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
