/* Finance controller shares the cached test-features page lifecycle. */
const financeController = (() => {
  const $ = id => document.getElementById(id);
  const html = value => { const node = document.createElement('span'); node.textContent = value == null ? '' : String(value); return node.innerHTML; };
  const t = value => window.SiteLanguage?.t(value) || value;
  const ui = value => `<span data-ui-text>${html(t(value))}</span>`;
  const money = cents => `${(cents / 100).toFixed(2)} ${t('元')}`;
  const currency = cents => new Intl.NumberFormat(document.documentElement.lang === 'en' ? 'en-US' : 'zh-CN', {style:'currency',currency:'CNY'}).format(cents/100);
  const cents = item => item.amount_cents ?? Math.round(Number(item.amount) * 100);
  const names = {pending:'待审核', approved:'已通过', rejected:'已驳回', cancelled:'已撤销', submitted:'提交申请', edited:'修改申请', resubmitted:'修改后重新提交', legacy:'旧申请导入'};
  const colors = ['#6558ed', '#28b9da', '#23b98c', '#f1a547', '#e67392', '#8798b4'];
  const pill = status => `<span class="status-pill status-${html(status)}" data-ui-text>${html(t(names[status] || status))}</span>`;
  const localDay = value => { const d = new Date(value); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
  const date = value => value ? new Date(value).toLocaleString(document.documentElement.lang === 'en' ? 'en-US' : 'zh-CN') : '—';
  const identity = item => `${item.username || ''}${item.user_note ? `（${item.user_note}）` : ''}`;
  const groupKey = item => item.expense_group_id || `legacy:${item.expense_group_name || '未分组'}`;
  let admin = false, tab = 'mine', mine = [], all = [], history = [], announcements = [], groups = [], review = null, page = 1;
  let selected = new Set(), loading = null;
  const request = async (url, options={}) => {
    const response = await fetch(url, {cache:'no-store', ...options});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(t(data.detail || '请求失败'));
    return data;
  };
  const json = (method, body) => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const error = (id, value='') => { $(id).textContent = t(value); };
  const show = id => { const dialog = $(id); if (!dialog.open) dialog.showModal(); };
  const hide = id => $(id).close();
  document.querySelectorAll('#view-finance .fin-dialog').forEach(dialog => {
    let startedOutside = false;
    const outside = event => {
      const rect = dialog.getBoundingClientRect();
      return event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom);
    };
    dialog.addEventListener('pointerdown', event => { startedOutside = outside(event); });
    dialog.addEventListener('click', event => {
      if (startedOutside && outside(event)) dialog.close();
      startedOutside = false;
    });
  });
  const currentItems = () => tab === 'mine' ? mine : tab === 'history' ? history.map(event => ({...event.snapshot, status:event.action, event, search_note:event.note, search_actor:event.actor_name})) : all;
  function options(id, values, label) {
    const previous = $(id).value;
    $(id).innerHTML = `<option value="">${html(t(label))}</option>` + [...new Map(values).entries()].map(([key,name]) => `<option value="${html(key)}" data-i18n-skip>${html(name)}</option>`).join('');
    if ([...$(id).options].some(option => option.value === previous)) $(id).value = previous;
  }
  function period() {
    const value = $('fin-period').value, now = new Date(), start = new Date(now);
    if (value === 'day') start.setHours(0,0,0,0);
    if (value === 'week') start.setDate(now.getDate() - ((now.getDay()+6)%7));
    if (value === 'month') start.setDate(1);
    $('fin-from').disabled = $('fin-to').disabled = value !== 'custom';
    $('fin-custom-range').hidden = value !== 'custom';
    $('fin-from').value = value === 'all' ? '' : value === 'custom' ? $('fin-from').value : localDay(start);
    $('fin-to').value = value === 'all' ? '' : value === 'custom' ? $('fin-to').value : localDay(now);
  }
  function filtered() {
    const query = $('fin-search').value.trim().toLocaleLowerCase(), status = $('fin-status').value;
    const from = $('fin-from').value, to = $('fin-to').value;
    const min = $('fin-min').value, max = $('fin-max').value;
    return currentItems().filter(item => {
      const day = localDay(item.event?.created_at || item.created_at);
      const text = [item.id, item.project, item.username, item.user_note, ...(item.groups || []), item.expense_group_name, item.review_note, item.search_note, item.search_actor].join(' ').toLocaleLowerCase();
      return (!query || text.includes(query)) && (status === 'all' || status === item.status)
        && (!from || day >= from) && (!to || day <= to) && (!from || !to || from <= to)
        && (min === '' || cents(item) >= Math.round(Number(min)*100)) && (max === '' || cents(item) <= Math.round(Number(max)*100))
        && (tab === 'mine' || !$('fin-group').value || groupKey(item) === $('fin-group').value)
        && (tab === 'mine' || !$('fin-user').value || item.username === $('fin-user').value)
        && (tab !== 'history' || !$('fin-reviewer').value || item.event.actor_id === $('fin-reviewer').value);
    });
  }
  function fileLink(item, kind, event=null) {
    const file = (item.files || []).find(file => file.kind === kind);
    if (!file) return `<span class="fin-secondary" data-ui-text>${t('未上传')}</span>`;
    const url = `/api/finance/applications/${encodeURIComponent(item.id)}/download?kind=${kind}${event ? `&event_id=${encodeURIComponent(event.id)}` : ''}`;
    const label = kind === 'payment' ? '支付记录' : /\.pdf$/i.test(file.original_name) ? 'PDF 发票' : '图片发票';
    return `<a class="fin-file" title="${html(file.original_name)}" href="${url}"><span data-ui-text>${t(label)}</span> ↓</a>`;
  }
  function tableRows(items, own) {
    return items.map(item => `<tr>
      <td><span class="fin-tag" data-i18n-skip title="${html(t('当前分组'))}: ${html((item.groups || []).join('、') || t('未分组'))}">${html(item.expense_group_name || t('未分组'))}</span></td>
      ${!own ? `<td data-i18n-skip>${html(identity(item))}</td>` : ''}
      <td class="fin-project" data-i18n-skip>${html(item.project)}<span class="fin-date" title="${html(item.id)}">${html(item.id.slice(0,8))} · ${localDay(item.created_at).slice(5)} · v${item.version || 1}</span></td><td class="fin-money">${html(currency(cents(item)))}</td>
      <td>${fileLink(item,'payment')}</td><td>${fileLink(item,'invoice')}</td><td>${pill(item.status)}${item.review_note ? `<small data-i18n-skip>${html(item.review_note)}</small>` : ''}</td>
      <td><div class="actions">
      ${own && ['pending','rejected'].includes(item.status) ? `<button type="button" data-edit-finance="${html(item.id)}">${t(item.status === 'rejected' ? '修改并重提' : '编辑')}</button>` : ''}
      ${own && item.status === 'pending' ? `<button type="button" data-cancel-finance="${html(item.id)}">${t('撤销')}</button>` : ''}
      ${!own && item.status === 'pending' ? `<button type="button" data-review="${html(item.id)}" data-result="approved">${t('通过')}</button><button type="button" data-review="${html(item.id)}" data-result="rejected">${t('驳回')}</button>` : ''}
      <button type="button" data-history="${html(item.id)}">${t('详情')}</button></div></td></tr>`).join('') || `<tr><td colspan="${own ? 7 : 8}" class="fin-secondary" data-ui-text>${t('没有匹配的申请。')}</td></tr>`;
  }
  function historyRows(items) {
    const days = new Map();
    for (const item of items) {
      const day = localDay(item.event.created_at);
      if (!days.has(day)) days.set(day, []);
      days.get(day).push(item);
    }
    return [...days].map(([day, rows]) => `<h3 class="fin-history-day">${html(day)} <small>· ${rows.length} ${t('笔审批')} · ${t('通过')} ${html(currency(rows.filter(item=>item.event.action==='approved').reduce((sum,item)=>sum+cents(item),0)))}</small></h3>
      <div class="card table-wrap"><table aria-label="${html(day)} ${t('审批记录')}"><thead><tr><th>${t('选择')}</th><th>${t('付款人 / 费用归属')}</th><th>${t('报销项目')}</th><th class="fin-money">${t('金额')}</th><th>${t('结果 / 意见')}</th><th>${t('审核人')}</th><th>${t('附件与历史')}</th></tr></thead><tbody>${rows.map(item => {
        const event = item.event;
        return `<tr><td>${event.action === 'approved' ? `<input class="fin-check" type="checkbox" aria-label="${html(t('选择审批记录'))}" data-select-event="${html(event.id)}" ${selected.has(event.id) ? 'checked' : ''} />` : '—'}</td>
          <td data-i18n-skip>${html(identity(item))}<span class="fin-date">${html(item.expense_group_name)}</span></td><td class="fin-project" data-i18n-skip>${html(item.project)}<span class="fin-date">${html(item.id.slice(0,8))} · v${event.version}</span></td><td class="fin-money">${html(currency(cents(item)))}</td><td>${pill(event.action)}<small data-i18n-skip>${html(event.note)}</small></td><td data-i18n-skip>${html(event.actor_name)}<span class="fin-date">${html(date(event.created_at))}</span></td><td><div class="actions">${fileLink(item,'invoice',event)} ${fileLink(item,'payment',event)}<button type="button" data-history="${html(item.id)}">${t('详情')}</button></div></td></tr>`;
      }).join('')}</tbody></table></div>`).join('') || `<p class="fin-secondary" data-ui-text>${t('暂无审批记录。')}</p>`;
  }
  function stats(items) {
    const counted = items.filter(item => item.status !== 'cancelled');
    const categories = [['申请总额',counted], ['待审核金额',counted.filter(item=>item.status==='pending')], ['已通过金额',counted.filter(item=>item.status==='approved')], ['已驳回金额',counted.filter(item=>item.status==='rejected')]];
    $('fin-stat-cards').innerHTML = categories.map(([label,rows]) => `<article class="stat"><span class="stat-label" data-ui-text>${t(label)}</span><div class="stat-value">${html(currency(rows.reduce((sum,item)=>sum+cents(item),0)))}</div><small>${rows.length} ${t('笔申请')}</small></article>`).join('');
    const grouped = new Map();
    const days = counted.map(item=>localDay(item.created_at)).sort();
    const monthly = days.length && (new Date(days.at(-1))-new Date(days[0])) / 86400000 > 62;
    for (const item of counted) { const key = localDay(item.created_at).slice(0,monthly ? 7 : 10); grouped.set(key,(grouped.get(key)||0)+cents(item)); }
    const points = [...grouped].sort(([a],[b])=>a.localeCompare(b));
    if (!points.length) $('fin-trend').innerHTML = `<p class="fin-secondary" data-ui-text>${t('所选范围暂无数据。')}</p>`;
    else {
      const maximum=Math.max(...points.map(([,amount])=>amount/100),1), unit=10**Math.floor(Math.log10(maximum)), ceiling=Math.ceil(maximum/unit)*unit;
      const width=660, height=225, left=48, top=24, plotHeight=165, step=(width-left-10)/points.length, barWidth=Math.min(42,step*.58);
      const grid=[0,ceiling/2,ceiling].map(value=>{const y=top+plotHeight-value/ceiling*plotHeight;return `<line x1="${left}" x2="650" y1="${y}" y2="${y}" stroke="var(--fin-line)"/><text x="${left-7}" y="${y+4}" text-anchor="end" fill="var(--fin-muted)" font-size="11">${value.toLocaleString(undefined,{maximumFractionDigits:0})}</text>`;}).join('');
      const bars=points.map(([day,amount],index)=>{const x=left+step*(index+.5),barHeight=amount/100/ceiling*plotHeight;return `<g tabindex="0" role="button" data-fin-chart="trend" aria-label="${html(day)} · ${html(currency(amount))}"><title>${html(day)}: ${html(currency(amount))}</title><rect x="${x-barWidth/2}" y="${top+plotHeight-barHeight}" width="${barWidth}" height="${barHeight}" rx="3" fill="var(--fin-accent)"/></g>${index%Math.ceil(points.length/7)===0?`<text x="${x}" y="212" text-anchor="middle" fill="var(--fin-muted)" font-size="11">${html(day.slice(monthly?0:5))}</text>`:''}`;}).join('');
      $('fin-trend').innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${t('申请金额趋势')}"><text x="40" y="12" text-anchor="end" fill="var(--fin-muted)" font-size="11">${t('元')}</text>${grid}${bars}</svg>` + chartDetails('trend', points);
    }
    const byStatus=$('fin-breakdown').value==='status', breakdown=new Map();
    if (byStatus) for (const status of ['pending','approved','rejected']) breakdown.set(status,0);
    for (const item of counted) { const key=byStatus?item.status:item.expense_group_name || t('未分组');breakdown.set(key,(breakdown.get(key)||0)+cents(item)); }
    const pieces=[...breakdown],total=pieces.reduce((sum,[,value])=>sum+value,0);let offset=0;
    const color=(key,index)=>byStatus?({pending:'var(--fin-text)',approved:'var(--fin-good)',rejected:'var(--fin-bad)'})[key]:colors[index%colors.length];
    const label=key=>byStatus?t(names[key]):key;
    $('fin-breakdown-title').textContent=t(byStatus?'申请状态金额占比':'金额分布');
    $('fin-donut').innerHTML = total ? `<svg class="fin-donut-svg" viewBox="0 0 360 225" role="img" aria-label="${t('金额分布')}">${pieces.map(([key,value],index)=>{const share=value/total*100,circle=value?`<g tabindex="0" role="button" data-fin-chart="donut" aria-label="${html(label(key))} · ${html(currency(value))} · ${share.toFixed(1)}%"><title>${html(label(key))}: ${html(currency(value))} (${share.toFixed(1)}%)</title><circle cx="180" cy="110" r="68" fill="none" stroke="${color(key,index)}" stroke-width="26" pathLength="100" stroke-dasharray="${share} ${100-share}" stroke-dashoffset="${-offset}" transform="rotate(-90 180 110)"/></g>`:'';offset+=share;return circle;}).join('')}<text x="180" y="110" text-anchor="middle" fill="var(--fin-text)" font-size="13" font-weight="600">${html(currency(total))}</text><text x="180" y="130" text-anchor="middle" fill="var(--fin-muted)" font-size="11">${t('不含撤销')}</text></svg><div class="fin-legend">${pieces.map(([key,value],index)=>`<span data-i18n-skip><i style="background:${color(key,index)}"></i>${html(label(key))} ${html(currency(value))} · ${(value/total*100).toFixed(1)}%</span>`).join('')}</div>` + chartDetails('donut',pieces.map(([key,value])=>[label(key),value])) : `<p class="fin-secondary" data-ui-text>${t('所选范围暂无数据。')}</p>`;
    for (const button of document.querySelectorAll('[data-fin-period]')) button.setAttribute('aria-pressed',String(button.dataset.finPeriod===$('fin-period').value));
    let range=[$('fin-from').value,$('fin-to').value].filter(Boolean).join(' – ');
    if($('fin-period').value==='month') range=new Intl.DateTimeFormat(document.documentElement.lang==='en'?'en-US':'zh-CN',{year:'numeric',month:'long'}).format(new Date($('fin-from').value+'T12:00:00'));
    if($('fin-period').value==='day') range=$('fin-from').value;
    $('fin-period-label').textContent=`${range || t('全部时间')} · ${t('按申请提交日')} · ${t('不含撤销')}`;
  }
  function chartDetails(name, rows) {
    return `<details class="fin-chart-details" id="fin-${name}-details"><summary>${t('查看金额明细')}</summary><div class="table-wrap"><table><thead><tr><th>${t(name==='trend'?'日期':'分类')}</th><th class="fin-money">${t('金额')}</th></tr></thead><tbody>${rows.map(([label,value])=>`<tr><td data-i18n-skip>${html(label)}</td><td class="fin-money">${html(currency(value))}</td></tr>`).join('')}</tbody></table></div></details>`;
  }
  function render() {
    const items = filtered();
    if (tab === 'notices') { renderNotices(); return; }
    $('fin-filter-summary').textContent = $('fin-from').value && $('fin-to').value && $('fin-from').value > $('fin-to').value ? t('开始日期不能晚于结束日期。') : `${tab==='mine'?t('仅本人')+' · ':''}${items.length} ${t('笔申请')} · ${t('合计')} ${currency(items.reduce((sum,item)=>sum+cents(item),0))}`;
    if (tab === 'stats') { stats(items); return; }
    const pages = Math.max(1,Math.ceil(items.length/25));page=Math.min(page,pages);
    const rows=items.slice((page-1)*25,page*25);
    const target = $(tab==='mine' ? 'finance-rows' : tab==='review' ? 'admin-finance-rows' : 'fin-history-rows');
    target.innerHTML = tab==='history' ? historyRows(rows) : tableRows(rows,tab==='mine');
    const card = tab==='history' ? $('fin-panel-history') : target.closest('.card');card.querySelector('.fin-pager')?.remove();
    if (pages>1) card.insertAdjacentHTML('beforeend',`<div class="fin-pager"><button type="button" data-fin-page="-1" ${page===1?'disabled':''}>${t('上一页')}</button><span>${page} / ${pages}</span><button type="button" data-fin-page="1" ${page===pages?'disabled':''}>${t('下一页')}</button></div>`);
    const eligible = items.filter(item=>item.event?.action==='approved').map(item=>item.event.id);
    selected=new Set([...selected].filter(id=>eligible.includes(id)));
    $('fin-select-all').checked=eligible.length>0 && eligible.every(id=>selected.has(id));
    $('fin-select-all').indeterminate=selected.size>0 && !$('fin-select-all').checked;
    $('finance-batch-download').disabled=!selected.size;
  }
  function selectTab(value, reset=true) {
    tab=value;page=1;selected.clear();
    for (const name of ['mine','review','stats','history','notices']) $('fin-panel-'+name).hidden = tab!==name;
    for (const button of $('finance-tabs').querySelectorAll('button')) button.setAttribute('aria-pressed',String(button.dataset.finTab===tab));
    $('finance-filter-card').hidden=tab==='notices';
    const destination=tab==='notices' ? $('fin-filter-parking') : $('fin-panel-'+tab).querySelector('.fin-filter-slot');
    destination.append($('finance-filter-card'));
    $('fin-refresh').hidden=tab!=='review';
    $('fin-reviewer-filter').hidden=tab!=='history';
    document.querySelectorAll('.fin-admin-filter').forEach(el=>el.classList.toggle('hidden',!admin || tab==='mine'));
    if (reset) { $('fin-more-filters').open=false;$('fin-search').value='';$('fin-status').value=tab==='review'?'pending':'all';$('fin-period').value=tab==='stats'?'month':'all';$('fin-min').value=$('fin-max').value=$('fin-group').value=$('fin-user').value=$('fin-reviewer').value='';period(); }
    render();
  }
  function renderNotices() {
    const latest=announcements[0];
    $('announcements').innerHTML=`${latest ? `<span class="fin-tag ${latest.pinned?'fin-pin':''}" data-ui-text>${t(latest.pinned?'置顶公告':'财务公告')}</span><button class="fin-notice-title" type="button" data-read-notice="${html(latest.id)}" data-i18n-skip>${html(latest.title)}</button>` : `<span class="fin-notice-title fin-secondary" data-ui-text>${t('暂无公告。')}</span>`}<button class="fin-link" type="button" id="fin-notice-history">${t('历史公告')} →</button>`;
    const query=$('fin-search').value.trim().toLocaleLowerCase();
    $('admin-announcements').innerHTML=announcements.filter(item=>tab!=='notices'||(item.title+' '+item.body).toLocaleLowerCase().includes(query)).map(item=>`<article class="announcement-admin-item"><div><h3><span data-i18n-skip>${html(item.title)}</span> ${item.pinned?`<span class="fin-tag fin-pin" data-ui-text>${t('置顶')}</span>`:''}</h3><small>${html(date(item.updated_at))}</small></div><div class="actions"><button class="fin-link" type="button" data-read-notice="${html(item.id)}">${t('阅览')}</button><button class="fin-link" type="button" data-edit-notice="${html(item.id)}">${t('编辑')}</button><button class="fin-link" type="button" data-pin-notice="${html(item.id)}">${t(item.pinned?'取消置顶':'置顶')}</button><button class="fin-link" type="button" data-delete-notice="${html(item.id)}">${t('删除')}</button></div></article>`).join('') || `<p class="fin-secondary" data-ui-text>${t('暂无公告。')}</p>`;
  }
  async function load() {
    if (loading) return loading;
    loading=(async()=>{
      const endpoints=['/api/finance/applications','/api/finance/announcements','/api/finance/groups'];
      if(admin) endpoints.push('/api/admin/finance/applications','/api/admin/finance/history');
      const values=await Promise.all(endpoints.map(url=>request(url)));
      [mine,announcements,groups]=values.slice(0,3).map(data=>data.items || []);
      if(admin) {all=values[3].items || [];history=values[4].items || [];}
      $('finance-total').textContent=mine.length;
      $('dashboard-pending').textContent=$('finance-pending').textContent=mine.filter(item=>item.status==='pending').length;
      $('finance-approved').textContent=money(mine.filter(item=>item.status==='approved').reduce((sum,item)=>sum+cents(item),0));
      $('dashboard-finance').innerHTML=mine.slice(0,3).map(item=>`<li><span data-i18n-skip>${html(item.project)}<small>${html(money(cents(item)))}</small></span>${pill(item.status)}</li>`).join('') || '<li data-ui-text>暂无报销申请</li>';
      if(admin) {$('dashboard-review-link').classList.remove('hidden');$('dashboard-review').textContent=all.filter(item=>item.status==='pending').length;}
      options('fin-group',all.map(item=>[groupKey(item),item.expense_group_name || '未分组']),'全部分组');
      options('fin-user',all.map(item=>[item.username,identity(item)]),'全部付款人');
      options('fin-reviewer',history.map(event=>[event.actor_id,event.actor_name]),'全部审核人');
      renderNotices();render();
    })();
    try {await loading;} finally {loading=null;}
  }
  function openApplication(item=null) {
    $('finance-form').reset();error('fin-form-status');
    $('finance-edit-id').value=item?.id || '';$('finance-version').value=item?.version || '';
    $('finance-project').value=item?.project || '';$('finance-amount').value=item?.amount || '';
    $('finance-invoice').required=!item;
    $('fin-invoice-label').textContent=t(item?'发票（不选则保留原文件）':'发票（必选）');
    $('finance-dialog-title').textContent=t(item ? item.status==='rejected'?'修改并重提':'编辑报销申请':'提交报销申请');
    $('finance-submit').textContent=t(item ? item.status==='rejected'?'修改并重提':'保存修改':'提交申请');
    $('finance-file-hint').textContent=t(item?'支持 PDF、JPG、PNG、WebP，单个文件不超过 10 MB。不选择文件则保留原文件。':'支持 PDF、JPG、PNG、WebP，单个文件不超过 10 MB。');
    for(const kind of ['invoice','payment']) {
      const existing=item?.files?.find(file=>file.kind===kind);
      $('fin-'+kind+'-name').textContent=existing?.original_name || '';
      $('fin-'+kind+'-name').title=existing?.original_name || '';
      $('fin-'+kind+'-prompt').textContent=t(existing ? (kind==='invoice'?'保留现有发票 / 替换':'保留现有支付记录 / 替换') : (kind==='invoice'?'＋ 选择发票 · PDF / JPG / PNG / WebP':'＋ 选择支付记录'));
    }
    const choices=[...groups];
    if(item?.expense_group_id && !choices.some(group=>group.id===item.expense_group_id)) choices.push({id:item.expense_group_id,name:item.expense_group_name});
    $('fin-expense-group').innerHTML='<option value="">'+t('请选择费用归属')+'</option>'+choices.map(group=>`<option value="${html(group.id)}" data-i18n-skip>${html(group.name)}</option>`).join('');
    $('fin-expense-label').hidden=groups.length<=1;
    $('fin-expense-group').required=groups.length>1;
    $('fin-expense-group').value=item?.expense_group_id || (groups.length===1?groups[0].id:'');
    $('fin-expense-hint').textContent=groups.length>1?t('多分组账户需选择一个费用归属，金额只计入该分组。'):`${t('费用归属')}：${item?.expense_group_name || groups[0]?.name || t('未分组')}`;
    show('finance-dialog');
  }
  function openNoticeForm(item=null) {
    $('announcement-form').reset();error('announcement-status');
    $('announcement-id').value=item?.id || '';$('announcement-title').value=item?.title || '';$('announcement-body').value=item?.body || '';$('announcement-pinned').checked=item?.pinned || false;
    $('announcement-form-title').textContent=t(item?'编辑公告':'新增公告');show('fin-announcement-dialog');
  }
  async function detail(appId) {
    const data=await request(`/api/finance/applications/${encodeURIComponent(appId)}/history`);
    $('fin-detail-title').textContent=t('申请详情与历史');
    $('fin-detail-content').innerHTML=data.items.map(event=>`<article class="fin-event"><strong>${ui(names[event.action] || event.action)} · v${event.version}</strong><small class="muted"> ${html(date(event.created_at))}</small><p data-i18n-skip>${html(event.actor_name)} · ${html(event.snapshot.project)} · ${html(money(cents(event.snapshot)))} · ${html(event.snapshot.expense_group_name)}</p>${event.note?`<p data-i18n-skip>${html(event.note)}</p>`:''}<div class="actions">${fileLink(event.snapshot,'invoice',event)} ${fileLink(event.snapshot,'payment',event)}</div></article>`).join('');show('fin-detail-dialog');
  }
  function noticeDetail(id) {
    const item=announcements.find(value=>value.id===id);if(!item)return;
    $('fin-detail-title').textContent=t('财务公告');$('fin-detail-content').innerHTML=`<h3 data-i18n-skip>${html(item.title)}</h3><small class="muted">${html(date(item.updated_at))}</small><p class="fin-prewrap" data-i18n-skip>${html(item.body)}</p>`;show('fin-detail-dialog');
  }
  $('finance-open-form').addEventListener('click',()=>openApplication());
  $('fin-new-notice').addEventListener('click',()=>openNoticeForm());
  for(const kind of ['invoice','payment']) $(kind==='invoice'?'finance-invoice':'finance-payment').addEventListener('change',event=>{
    const name=event.target.files[0]?.name || '';
    $('fin-'+kind+'-name').textContent=name;
    $('fin-'+kind+'-name').title=name;
  });
  $('finance-tabs').addEventListener('click',event=>{const button=event.target.closest('[data-fin-tab]');if(button)selectTab(button.dataset.finTab);});
  $('fin-reset').addEventListener('click',()=>selectTab(tab));
  $('fin-refresh').addEventListener('click',()=>load().catch(e=>error('finance-status',e.message)));
  $('fin-period').addEventListener('change',()=>{period();page=1;render();});
  for(const id of ['fin-search','fin-min','fin-max']) $(id).addEventListener('input',()=>{page=1;render();});
  for(const id of ['fin-status','fin-from','fin-to','fin-group','fin-user','fin-reviewer','fin-breakdown']) $(id).addEventListener('change',()=>{page=1;render();});
  $('view-finance').addEventListener('click',async event=>{
    const chart=event.target.closest('[data-fin-chart]');
    if(chart){$('fin-'+chart.dataset.finChart+'-details').open=true;return;}
    const button=event.target.closest('button');if(!button)return;
    try {
      if(button.dataset.finPeriod){$('fin-period').value=button.dataset.finPeriod;period();page=1;render();}
      if(button.dataset.closeDialog)hide(button.dataset.closeDialog);
      if(button.dataset.finPage){page+=Number(button.dataset.finPage);render();}
      if(button.dataset.history)await detail(button.dataset.history);
      if(button.dataset.editFinance)openApplication(mine.find(item=>item.id===button.dataset.editFinance));
      if(button.dataset.cancelFinance){const item=mine.find(item=>item.id===button.dataset.cancelFinance);if(confirm(t('撤销这条待审核申请？'))){await request(`/api/finance/applications/${encodeURIComponent(item.id)}/cancel?version=${item.version}`,{method:'POST'});await load();}}
      if(button.dataset.review){const item=all.find(item=>item.id===button.dataset.review);review={item,status:button.dataset.result};$('fin-review-form').reset();error('fin-review-error');$('fin-review-title').textContent=t(review.status==='rejected'?'驳回申请':'通过申请');$('fin-review-summary').textContent=`${identity(item)} · ${item.project} · ${money(cents(item))} · v${item.version}`;$('fin-review-note').required=review.status==='rejected';$('fin-review-note').placeholder=t(review.status==='rejected'?'请填写驳回原因':'审核备注（可选）');show('fin-review-dialog');}
      if(button.dataset.readNotice)noticeDetail(button.dataset.readNotice);
      if(button.id==='fin-notice-history'){$('fin-detail-title').textContent=t('历史公告');$('fin-detail-content').innerHTML=announcements.map(item=>`<article class="fin-event"><button type="button" data-read-notice="${html(item.id)}" data-i18n-skip>${html(item.title)}</button><small class="muted"> ${html(date(item.updated_at))}</small></article>`).join('') || t('暂无公告。');show('fin-detail-dialog');}
      if(button.dataset.editNotice)openNoticeForm(announcements.find(item=>item.id===button.dataset.editNotice));
      if(button.dataset.pinNotice){const item=announcements.find(item=>item.id===button.dataset.pinNotice);await request(`/api/admin/finance/announcements/${item.id}`,json('PUT',{title:item.title,body:item.body,pinned:!item.pinned}));await load();}
      if(button.dataset.deleteNotice && confirm(t('删除这条公告？'))){await request(`/api/admin/finance/announcements/${button.dataset.deleteNotice}`,{method:'DELETE'});await load();}
    }catch(e){error('finance-status',e.message);}
  });
  $('view-finance').addEventListener('keydown',event=>{
    const chart=event.target.closest('[data-fin-chart]');
    if(chart && ['Enter',' '].includes(event.key)){event.preventDefault();$('fin-'+chart.dataset.finChart+'-details').open=true;}
  });
  async function submit(buttonId,statusId,action) {
    const button=$(buttonId);if(button.disabled)return;button.disabled=true;error(statusId);
    try {await action();}catch(e){error(statusId,e.message);}finally{button.disabled=false;}
  }
  $('finance-form').addEventListener('submit',event=>{event.preventDefault();submit('finance-submit','fin-form-status',async()=>{
    if([...$('finance-project').value.trim()].length>20)throw new Error(t('报销项目最多 20 个字符。'));
    const form=new FormData(),id=$('finance-edit-id').value;
    for(const [key,field] of [['project','finance-project'],['amount','finance-amount'],['expense_group_id','fin-expense-group']])form.set(key,$(field).value);
    if(id)form.set('version',$('finance-version').value);
    for(const [key,field] of [['invoice','finance-invoice'],['payment_record','finance-payment']]){const file=$(field).files[0];if(file){if(file.size>10*1024*1024)throw new Error(t('单个报销文件不能超过 10 MB'));form.set(key,file);}}
    await request('/api/finance/applications'+(id?`/${encodeURIComponent(id)}`:''),{method:id?'PUT':'POST',body:form});hide('finance-dialog');await load();error('finance-status',id?'申请已更新。':'申请已提交。');
  });});
  $('fin-review-form').addEventListener('submit',event=>{event.preventDefault();submit('fin-review-submit','fin-review-error',async()=>{
    if(review.status==='rejected' && !$('fin-review-note').value.trim())throw new Error(t('请填写驳回原因'));
    await request(`/api/admin/finance/applications/${encodeURIComponent(review.item.id)}/review`,json('POST',{status:review.status,note:$('fin-review-note').value,version:review.item.version}));hide('fin-review-dialog');await load();
  });});
  $('announcement-form').addEventListener('submit',event=>{event.preventDefault();submit('announcement-submit','announcement-status',async()=>{
    const id=$('announcement-id').value;await request('/api/admin/finance/announcements'+(id?`/${id}`:''),json(id?'PUT':'POST',{title:$('announcement-title').value,body:$('announcement-body').value,pinned:$('announcement-pinned').checked}));hide('fin-announcement-dialog');await load();
  });});
  $('fin-history-rows').addEventListener('change',event=>{const id=event.target.dataset.selectEvent;if(id){event.target.checked?selected.add(id):selected.delete(id);render();}});
  $('fin-select-all').addEventListener('change',event=>{selected=new Set(event.target.checked?filtered().filter(item=>item.event.action==='approved').map(item=>item.event.id):[]);render();});
  $('finance-batch-download').addEventListener('click',()=>submit('finance-batch-download','finance-status',async()=>{
    if(!selected.size)return;
    const response=await fetch('/api/admin/finance/download.zip',json('POST',{event_ids:[...selected]}));
    if(!response.ok){const data=await response.json();throw new Error(data.detail || t('批量下载失败'));}
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download='approved-finance.zip';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }));
  document.addEventListener('site:language',()=>{renderNotices();render();});
  return {load,start(value){admin=value;$('finance-admin').classList.toggle('hidden',!admin);$('finance-tabs').classList.toggle('hidden',!admin);$('fin-new-notice').classList.toggle('hidden',!admin);$('fin-subtitle').textContent=t(admin?'审核申请与管理报销资料':'查看公告、提交报销和跟进审核');selectTab(admin?'review':'mine');}};
})();
