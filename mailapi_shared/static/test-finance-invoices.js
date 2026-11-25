/* Local invoice assistance and separately audited, manual tax verification. */
window.FinanceInvoices = (() => {
  const $=id=>document.getElementById(id),t=text=>window.SiteLanguage?.t(text)||text;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const names={number:'发票号码',code:'发票代码',date:'开票日期',total:'价税合计（元）',check_code:'校验码（可选）'};
  const results={unverified:'待税务查验',verified:'查验通过',inconsistent:'票面信息不一致',not_found:'查无此票',voided:'发票已作废',unavailable:'暂无法查验'};
  let admin=false,editingId='',hasPayment=false,analysis=null,analysisSeq=0,detailSeq=0,current=null,detailAbort=null;
  const touched=new Set();
  const input=(prefix,key)=>$(prefix+'-'+key.replace('_','-'));
  const values=prefix=>Object.fromEntries(Object.keys(names).map(key=>[key,input(prefix,key).value.trim()]));
  function message(id,text=''){ $(id).textContent=t(text); }
  async function api(url,options={}){
    const response=await fetch(url,{cache:'no-store',...options});const data=await response.json();
    if(!response.ok)throw new Error(t(typeof data.detail==='string'?data.detail:'请求失败'));return data;
  }
  function format(prefix){
    const digital=input(prefix,'number').value.replace(/\s/g,'').length===20;
    input(prefix,'code').required=!digital;input(prefix,'code').disabled=digital;
    if(digital)input(prefix,'code').value='';
  }
  function fillValues(prefix,fields,onlyUntouched=false){
    for(const key of Object.keys(names))if(!onlyUntouched || !touched.has(key))input(prefix,key).value=fields?.[key]||'';
    format(prefix);
  }
  function warnAmount(){
    const invoice=Number(input('fin-inv','total').value);
    $('finance-payment').required=invoice>=1000&&!hasPayment;
    message('fin-inv-warning',invoice<0?'红字金额，请管理员核对报销依据':'');
  }
  function fill(item){
    analysis?.abort();analysis=null;analysisSeq++;editingId=item?.id||'';touched.clear();
    hasPayment=!!item?.files?.some(file=>file.kind==='payment'&&file.size>0);
    fillValues('fin-inv',item?.invoice?.fields);$('fin-inv-confirm').checked=false;
    message('fin-inv-status');message('fin-inv-warning');
    $('fin-inv-recognize').disabled=false;$('finance-submit').disabled=false;warnAmount();
  }
  async function recognizeSelected(){
    const file=window.FinanceDrafts?.file('invoice')||$('finance-invoice').files[0];if(!file&&!editingId){message('fin-inv-status','请先选择发票文件');return;}
    analysis?.abort();const abort=new AbortController(),seq=++analysisSeq;analysis=abort;
    const form=new FormData();if(file)form.set('invoice',file);if(editingId)form.set('app_id',editingId);
    $('fin-inv-recognize').disabled=true;$('finance-submit').disabled=true;$('fin-inv-confirm').checked=false;
    message('fin-inv-status','正在识别发票…');
    try{
      const data=await api('/api/finance/invoices/analyze',{method:'POST',body:form,signal:abort.signal});
      if(abort.signal.aborted||seq!==analysisSeq||!$('finance-dialog').open)return;
      if(data.recognition.multiple){message('fin-inv-status','一笔申请仅支持一张发票，请拆分文件后提交');return;}
      fillValues('fin-inv',data.recognition.fields,true);
      document.dispatchEvent(new CustomEvent('finance:invoice-filled'));
      message('fin-inv-status',data.duplicate?'该发票已被待审核或已通过的申请占用，不能重复报销':data.recognition.message==='请核对识别信息'?'':data.recognition.message);warnAmount();
    }catch(error){if(!abort.signal.aborted&&seq===analysisSeq)message('fin-inv-status',error.message);}
    finally{if(seq===analysisSeq){analysis=null;$('fin-inv-recognize').disabled=false;$('finance-submit').disabled=false;}}
  }
  function getFields(){
    if(analysis)throw new Error(t('请等待发票识别完成'));
    if(!$('fin-inv-confirm').checked)throw new Error(t('请核对发票信息并勾选确认'));
    if(Number(input('fin-inv','total').value)>=1000&&!hasPayment&&!(window.FinanceDrafts?.file('payment')||$('finance-payment').files[0])?.size)throw new Error(t('价税合计满 1000 元，必须上传支付记录'));
    return values('fin-inv');
  }
  for(const key of Object.keys(names))input('fin-inv',key).addEventListener('input',()=>{touched.add(key);$('fin-inv-confirm').checked=false;format('fin-inv');warnAmount();});
  $('finance-invoice').addEventListener('change',()=>{
    analysis?.abort();analysis=null;analysisSeq++;touched.clear();fillValues('fin-inv',{});$('fin-inv-confirm').checked=false;message('fin-inv-warning');
    $('fin-inv-recognize').disabled=false;$('finance-submit').disabled=false;message('fin-inv-status');warnAmount();
    if($('finance-invoice').files[0])recognizeSelected();
  });
  $('fin-inv-recognize').addEventListener('click',()=>{touched.clear();recognizeSelected();});
  $('finance-dialog').addEventListener('close',()=>{analysis?.abort();analysis=null;analysisSeq++;$('fin-inv-recognize').disabled=false;$('finance-submit').disabled=false;$('finance-invoice').value='';});

  const fieldList=fields=>`<dl class="fin-invoice-details">${Object.entries(names).map(([key,label])=>`<div><dt>${esc(t(label))}</dt><dd data-i18n-skip>${esc(fields?.[key]||'—')}</dd></div>`).join('')}</dl>`;
  function renderCheck(item,checks){
    const invoice=item.invoice,verification=invoice?.verification;
    $('fin-check-summary').innerHTML=`<p data-i18n-skip>${esc(item.username)} · ${esc(item.project)} · v${item.version}</p>${invoice?fieldList(invoice.fields):`<p>${esc(t('历史申请尚未录入票面信息'))}</p>`}<p class="fin-invoice-result">${esc(t(verification?results[verification.status]:'待税务查验'))}${verification?.created_at?` · ${esc(t('人工登记'))} · <span data-i18n-skip>${esc(verification.actor_name)} · ${esc(new Date(verification.created_at).toLocaleString(document.documentElement.lang))}</span>`:''}</p>${verification?.note?`<p data-i18n-skip>${esc(verification.note)}</p>`:''}<div class="actions"><a href="/api/finance/applications/${encodeURIComponent(item.id)}/download?kind=invoice">${esc(t('下载发票'))} ↓</a></div>`;
    $('fin-invoice-check-form').hidden=!admin;$('fin-check-legacy').hidden=!!invoice||!admin;
    for(const node of $('fin-check-legacy').querySelectorAll('input'))node.disabled=!!invoice||!admin;
    if(!invoice&&admin)fillValues('fin-check',{});
    $('fin-check-result').value='';$('fin-check-note').value='';
    $('fin-check-history').innerHTML=checks.map(check=>`<article class="fin-event"><strong>${esc(t('人工登记'))} · ${esc(t(results[check.status]))}</strong><small data-i18n-skip>${esc(check.actor_name)} · ${esc(new Date(check.created_at).toLocaleString(document.documentElement.lang))} · v${check.version}</small><p data-i18n-skip>${esc(check.fields.number)} · ${esc(check.note)}</p>${check.file_id!==invoice?.file_id?`<small>${esc(t('历史发票附件'))}</small>`:''}</article>`).join('')||`<p class="fin-secondary">${esc(t('暂无税务查验记录'))}</p>`;
  }
  async function open(appId){
    detailAbort?.abort();const abort=new AbortController(),seq=++detailSeq;detailAbort=abort;current=null;
    $('fin-check-save').disabled=false;$('fin-check-recognize').disabled=false;
    $('fin-check-summary').replaceChildren();$('fin-check-history').replaceChildren();$('fin-invoice-check-form').hidden=true;
    message('fin-check-status','加载中…');if(!$('fin-invoice-dialog').open)$('fin-invoice-dialog').showModal();
    try{
      const data=await api(`/api/finance/applications/${encodeURIComponent(appId)}/invoice`,{signal:abort.signal});
      if(abort.signal.aborted||seq!==detailSeq||!$('fin-invoice-dialog').open)return;
      current=data.item;renderCheck(current,data.checks);message('fin-check-status');
    }catch(error){if(!abort.signal.aborted&&seq===detailSeq)message('fin-check-status',error.message);}
  }
  $('view-finance').addEventListener('click',event=>{const button=event.target.closest('[data-invoice-check]');if(button)open(button.dataset.invoiceCheck);});
  $('fin-check-refresh').addEventListener('click',()=>{if(current)open(current.id);});
  $('fin-check-copy').addEventListener('click',async()=>{
    if(!current)return;
    const fields=current.invoice?.fields||values('fin-check');
    try{await navigator.clipboard.writeText(Object.entries(names).map(([key,label])=>t(label)+': '+(fields[key]||'—')).join('\n'));message('fin-check-status','查验信息已复制');}
    catch{message('fin-check-status','请选中票面信息手动复制');}
  });
  for(const key of ['number','code'])input('fin-check',key).addEventListener('input',()=>format('fin-check'));
  $('fin-check-recognize').addEventListener('click',async()=>{
    if(!current)return;const item=current,seq=detailSeq;const form=new FormData();form.set('app_id',item.id);$('fin-check-recognize').disabled=true;message('fin-check-status','正在本地识别发票…');
    try{const data=await api('/api/finance/invoices/analyze',{method:'POST',body:form,signal:detailAbort.signal});if(seq===detailSeq&&$('fin-invoice-dialog').open){for(const key of Object.keys(names))if(!input('fin-check',key).value)input('fin-check',key).value=data.recognition.fields[key]||'';format('fin-check');message('fin-check-status',data.recognition.message);}}
    catch(error){if(seq===detailSeq&&error.name!=='AbortError')message('fin-check-status',error.message);}
    finally{if(seq===detailSeq)$('fin-check-recognize').disabled=false;}
  });
  $('fin-invoice-check-form').addEventListener('submit',async event=>{
    event.preventDefault();if(!current||$('fin-check-save').disabled)return;
    const item=current,seq=detailSeq;$('fin-check-save').disabled=true;message('fin-check-status');
    try{
      const payload={status:$('fin-check-result').value,note:$('fin-check-note').value,version:item.version,revision:item.invoice?.verification.revision||0};
      if(!item.invoice)payload.fields=values('fin-check');
      await api(`/api/admin/finance/applications/${encodeURIComponent(item.id)}/invoice/verify`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:detailAbort.signal});
      if(seq!==detailSeq||!$('fin-invoice-dialog').open)return;
      document.dispatchEvent(new CustomEvent('finance:changed'));await open(item.id);if(current?.id===item.id&&$('fin-invoice-dialog').open)message('fin-check-status','查验结果已登记，审批状态未更改');
    }catch(error){if(seq===detailSeq&&error.name!=='AbortError')message('fin-check-status',error.message);}
    finally{if(seq===detailSeq)$('fin-check-save').disabled=false;}
  });
  $('fin-invoice-dialog').addEventListener('close',()=>{detailAbort?.abort();detailSeq++;current=null;$('fin-check-save').disabled=false;$('fin-check-recognize').disabled=false;$('fin-check-summary').replaceChildren();$('fin-check-history').replaceChildren();$('fin-invoice-check-form').reset();});
  window.addEventListener('hashchange',()=>{if(location.hash!=='#view-finance'){if($('fin-invoice-dialog').open)$('fin-invoice-dialog').close();}});
  return {start(value){admin=value;},fill,getFields,
    summary(item){const status=item.invoice?.verification.status||'unverified';return `<button type="button" class="fin-link fin-invoice-check-link" data-invoice-check="${esc(item.id)}">${esc(t(results[status]))}</button>`;}};
})();
