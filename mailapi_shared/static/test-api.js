const apiController = (() => {
  const $ = id => document.getElementById(id);
  const t = value => window.SiteLanguage?.t(value) || value;
  const esc = value => { const node = document.createElement('span'); node.textContent = value == null ? '' : String(value); return node.innerHTML.replace(/"/g,'&quot;').replace(/'/g,'&#39;'); };
  const api = async (url, options = {}) => {
    const response = await fetch(url, {cache:'no-store',...options});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error?.message || `HTTP ${response.status}`);
    return data;
  };
  const send = (url, method, payload) => api(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const num = value => Number(value || 0).toLocaleString(document.documentElement.lang === 'en' ? 'en-US' : 'zh-CN');
  const date = value => value ? new Date(value).toLocaleString(document.documentElement.lang === 'en' ? 'en-US' : 'zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
  const baseUrl = `${location.origin}/api2api/v1`;
  let isAdmin = false, activeTab = 'overview', keys = [], usage = {items:[]}, engine = {}, quotaUser = null, revokeKey = null, keyDialogVersion = 0, adminUsage = [], adminGatewayReady = false, limitsKey = null, limitsVersion = 0;
  function message(id, value, success = false) { $(id).textContent = t(value); $(id).classList.toggle('success',success); }
  function tab(name) {
    if ((name === 'settings' || name === 'audit') && !isAdmin) return;
    activeTab = name;
    apiRecordsController.activate(name);
    document.querySelectorAll('[data-api-tab]').forEach(node => node.setAttribute('aria-pressed',String(node.dataset.apiTab === name)));
    document.querySelectorAll('[data-api-panel]').forEach(node => { node.hidden = node.dataset.apiPanel !== name; });
  }
  function renderKeys() {
    const query = $('api-key-search').value.trim().toLowerCase();
    const state = $('api-key-filter').value;
    const filtered = keys.filter(row => {
      const category = row.revoked ? 'revoked' : row.available_here===false || row.remote_status===3 || row.remote_status===4 || row.can_toggle===false ? 'unavailable' : row.enabled ? 'active' : 'paused';
      return `${row.name} ${row.key_prefix}`.toLowerCase().includes(query) && (state==='all' || state===category);
    });
    $('api-key-count').textContent = `${num(filtered.length)} / ${num(keys.length)} ${t('条密钥')}`;
    const icon = name => `<svg aria-hidden="true" viewBox="0 0 24 24"><use href="/static/test-api-icons.svg#${name}"></use></svg>`;
    const relative = value => {
      if(!value)return '—';
      const age=(new Date(value).getTime()-Date.now())/1000;
      if(!Number.isFinite(age))return '—';
      const [unit,step]=Math.abs(age)<3600?['minute',60]:Math.abs(age)<86400?['hour',3600]:['day',86400];
      return new Intl.RelativeTimeFormat(document.documentElement.lang==='en'?'en':'zh-CN',{numeric:'auto'}).format(Math.round(age/step),unit);
    };
    $('api-key-list').innerHTML = filtered.map(row => {
      const status=row.available_here===false?'其他网关密钥':row.revoke_pending?'撤销待同步':row.revoked?'密钥已撤销':row.pause_pending?'停用待同步':row.enabled?'可用':row.remote_status===3?'已到期':row.remote_status===4?'额度不足':row.can_toggle===false?'已失效':'已停用';
      const toggle=row.pause_pending?'重试停用':row.enabled?'停用':'启用';
      const actions=[
        row.can_toggle?`<button type="button" class="api-icon-button ${row.enabled?'api-bad':'api-good'}" data-toggle-key="${esc(row.id)}" data-key-enabled="${row.enabled || row.pause_pending?'false':'true'}" title="${esc(t(toggle))}" aria-label="${esc(t(toggle)+' '+row.name)}">${icon('power')}</button>`:'',
        row.can_edit?`<button type="button" class="api-icon-button" data-edit-key="${esc(row.id)}" title="${esc(t('编辑限制'))}" aria-label="${esc(t('编辑限制')+' '+row.name)}">${icon('edit')}</button>`:'',
        row.can_copy || row.revocable || row.enabled || row.revoke_pending?`<button type="button" class="api-icon-button" data-more-key="${esc(row.id)}" title="${esc(t('更多操作'))}" aria-label="${esc(t('更多操作')+' '+row.name)}">${icon('more')}</button>`:'',
      ].join('');
      const models=typeof row.model_limits==='string'?(row.model_limits || t('无限制')):'—';
      const ips=typeof row.allowed_ips==='string'?(row.allowed_ips || t('无限制')):'—';
      const expiry=row.expired_at===-1?t('永不'):typeof row.expired_at==='number'?new Date(row.expired_at*1000).toLocaleDateString(document.documentElement.lang==='en'?'en-US':'zh-CN'):'—';
      return `<tr><td><span data-i18n-skip>${esc(row.name)}</span><small data-i18n-skip>${esc(row.key_prefix)}…</small></td><td><span class="${row.enabled?'api-good':'api-dim'}" data-ui-text>${esc(t(status))}</span></td><td class="api-number">${row.provider==='newapi'?num(row.used_quota):'—'}</td><td><span class="api-key-limits" title="${esc(models)}" data-ui-text>${esc(models)}</span></td><td><span class="api-key-limits" title="${esc(ips)}" data-ui-text>${esc(ips)}</span></td><td class="api-key-time"><small><span>${esc(t('创建时间'))}</span><time title="${esc(date(row.created_at))}" data-i18n-skip>${esc(relative(row.created_at))}</time></small><small><span>${esc(t('最后使用'))}</span><time title="${esc(date(row.last_used_at))}" data-i18n-skip>${esc(relative(row.last_used_at))}</time></small></td><td class="api-key-expiry" data-ui-text>${esc(expiry)}</td><td><div class="api-key-actions">${actions || '—'}</div></td></tr>`;
    }).join('') || `<tr><td colspan="8" class="api-empty" data-ui-text>${esc(t('没有符合条件的密钥。'))}</td></tr>`;
    $('api-active-keys').textContent = num(keys.filter(row => row.enabled).length);
    $('dashboard-keys').textContent = keys.filter(row => row.enabled).length;
    apiRecordsController.setKeys(keys);
  }
  function renderUsage() {
    for (const [id, field] of Object.entries({'dashboard-requests':'requests','dashboard-tokens':'total_tokens','dashboard-input':'prompt_tokens','dashboard-output':'completion_tokens','api-metric-requests':'requests','api-metric-tokens':'total_tokens','api-input-total':'prompt_tokens','api-output-total':'completion_tokens'})) $(id).textContent = num(usage[field]);
    const split = usage.prompt_tokens + usage.completion_tokens;
    $('usage-ring').classList.toggle('has-data',split > 0);
    $('usage-ring').style.setProperty('--input-share',`${split ? 100 * usage.prompt_tokens / split : 0}%`);
    $('dashboard-calls').innerHTML = usage.items.slice(0,3).map(row => `<li><span data-i18n-skip>${esc(row.model || '—')}<small>${date(row.created_at)}</small></span><b>${num(row.total_tokens)} Token</b></li>`).join('') || `<li data-ui-text>${esc(t('暂无调用记录'))}</li>`;
    $('api-metric-duration').textContent = usage.items.length ? `${num(Math.round(usage.items.reduce((sum,row) => sum + row.request_ms,0) / usage.items.length))} ms` : '—';
  }
  function renderEngine() {
    $('api-engine-description').textContent = t('密钥由网关签发，本站管理入口和用户身份。');
    $('api-metric-quota').textContent = engine.linked ? num(engine.quota) : '—';
    $('api-quota-hint').textContent = t(engine.linked ? 'quota 点 · 以网关为准' : '创建密钥后关联独立网关账户');
    $('api-models').innerHTML = engine.models?.length ? engine.models.map(name => `<span data-i18n-skip>${esc(name)}</span>`).join('') : `<p class="api-hint" data-ui-text>${esc(t('创建密钥后获取可用模型，或由管理员配置渠道。'))}</p>`;
    const series = engine.series || [], totals = new Map();
    for (const row of series) {
      const day = new Date(row.created_at * 1000).toISOString().slice(0,10);
      const previous = totals.get(day) || {tokens:0,requests:0};
      totals.set(day,{tokens:previous.tokens + row.tokens,requests:previous.requests + row.requests});
    }
    if (!totals.size) { $('api-chart').innerHTML = `<p class="api-empty" data-ui-text>${esc(t('暂无网关聚合数据，发起调用后将在此显示。'))}</p>`; return; }
    const days = Array.from({length:30},(_,i) => { const day = new Date(); day.setUTCDate(day.getUTCDate()-29+i); return day.toISOString().slice(0,10); });
    const max = Math.max(1,...days.map(day => totals.get(day)?.tokens || 0));
    $('api-chart').innerHTML = `<svg viewBox="0 0 600 170" role="img" aria-label="${esc(t('最近 30 天 Token 用量'))}"><line x1="0" y1="139" x2="600" y2="139"/>${days.map((day,i) => { const row = totals.get(day) || {tokens:0,requests:0}, height = 115 * row.tokens / max; return `<rect x="${i*20+3}" y="${139-height}" width="12" height="${Math.max(height,1)}" rx="3" tabindex="0"><title>${day}: ${num(row.tokens)} Token · ${num(row.requests)} ${esc(t('请求'))}</title></rect>${[0,14,29].includes(i) ? `<text x="${i*20}" y="163" text-anchor="${i===29 ? 'end' : 'start'}">${day.slice(5)}</text>` : ''}`; }).join('')}</svg>`;
  }
  async function load() {
    const results = await Promise.allSettled([
      api('/api/api2api/keys').then(data => { keys = data.items; renderKeys(); }),
      api('/api/api2api/usage').then(data => { usage = data; renderUsage(); }),
      api('/api/api2api/engine').then(data => { engine = data || {}; renderEngine(); }),
    ]);
    const failure = results.find(result => result.status === 'rejected');
    if (failure) message('api-key-status',failure.reason.message);
    await apiRecordsController.refresh();
  }
  async function loadChannels() {
    if (!adminGatewayReady) { $('api-channels').textContent = t('请先配置并启用网关连接。'); return; }
    try {
      const data = await api('/api/admin/api2api/channels');
      $('api-channels').innerHTML = data.items.map(row => `<div class="api-channel"><div><strong data-i18n-skip>${esc(row.name)}</strong><span class="${row.status===1 ? 'api-good' : 'api-dim'}" data-ui-text>${esc(t(row.status===1 ? '启用' : '停用'))}</span></div><p data-i18n-skip>${esc(row.base_url)}</p><p data-i18n-skip>${esc(row.models)}</p></div>`).join('') || `<p class="api-empty" data-ui-text>${esc(t('暂无渠道，请添加上游连接。'))}</p>`;
    } catch (error) { $('api-channels').textContent = t(error.message); }
  }
  function renderAdminUsage() {
    const query = $('api-user-search').value.trim().toLowerCase();
    const filtered = adminUsage.filter(row => `${row.username || row.user_id} ${row.user_note || ''}`.toLowerCase().includes(query));
    $('api-user-count').textContent = `${num(filtered.length)} / ${num(adminUsage.length)} ${t('位用户')}`;
    $('admin-api-usage-rows').innerHTML = filtered.map(row => `<tr><td data-i18n-skip>${esc(row.username || row.user_id)}<small>${esc(row.user_note)}</small></td><td class="api-number">${num(row.requests)}</td><td class="api-number">${num(row.prompt_tokens)}</td><td class="api-number">${num(row.completion_tokens)}</td><td class="api-number">${num(row.total_tokens)}</td><td><button type="button" class="api-link" data-quota-user="${esc(row.user_id)}" data-quota-name="${esc(row.username || row.user_id)}" ${adminGatewayReady?'':'disabled'}>${esc(t('调整额度'))}</button></td></tr>`).join('') || `<tr><td colspan="6" class="api-empty" data-ui-text>${esc(t(query?'没有符合条件的用户。':'创建密钥或发起调用后显示用户。'))}</td></tr>`;
  }
  async function loadAdmin() {
    const [config,data] = await Promise.all([api('/api/admin/api2api/config'),api('/api/admin/api2api/usage')]);
    const native = config.configured && config.engine==='newapi';
    adminGatewayReady = native && config.enabled===true;
    $('api-config-url').value = native ? config.base_url : '';
    $('api-config-enabled').checked = adminGatewayReady;
    $('api-config-models').value = native ? (config.model_allowlist || []).join(',') : '';
    $('api-config-username').value = native ? config.management_username : '';
    $('api-config-quota').value = native ? config.initial_quota || 0 : 0;
    $('api-connection-status').textContent = t(native ? adminGatewayReady ? '已启用' : '已停用' : '未配置');
    $('api-connection-status').classList.toggle('api-good',adminGatewayReady);
    $('api-connection-url').textContent = native ? config.base_url : t('尚未配置网关连接');
    $('api-connection-hint').textContent = t(native ? '上游地址和密钥请在渠道中配置。' : '请填写独立网关连接；历史密钥和记录继续保留。');
    $('api-open-channel').disabled = !adminGatewayReady;
    $('api-verify').disabled = !adminGatewayReady;
    adminUsage = data.items;
    renderAdminUsage();
    await loadChannels();
  }
  async function copy(value, id) {
    try { await navigator.clipboard.writeText(value); $(id).textContent = t('已复制'); }
    catch { $(id).textContent = t('请选中内容手动复制'); }
  }
  function clearSecrets() {
    keyDialogVersion++;
    $('new-api-key').textContent = '';
    $('api-key-copy-status').textContent = '';
    $('api-key-result').hidden = true;
    $('api-key-form').hidden = false;
    $('api-key-form').reset();
    $('api-config-password').value = '';
    $('api-channel-key').value = '';
  }
  $('api2api-base-url').textContent = baseUrl;
  $('copy-base-url').addEventListener('click',() => copy(baseUrl,'copy-url-status'));
  $('api-copy-key').addEventListener('click',() => copy($('new-api-key').textContent,'api-key-copy-status'));
  $('api-copy-example').addEventListener('click',() => copy($('api-example').textContent,'api-example-status'));
  document.querySelectorAll('[data-api-tab]').forEach(node => node.addEventListener('click',() => tab(node.dataset.apiTab)));
  document.querySelectorAll('[data-api-go]').forEach(node => node.addEventListener('click',() => tab(node.dataset.apiGo)));
  document.querySelectorAll('[data-api-close]').forEach(node => node.addEventListener('click',() => $(node.dataset.apiClose).close()));
  $('api-key-dialog').addEventListener('close',clearSecrets);
  $('api-channel-dialog').addEventListener('close',() => $('api-channel-form').reset());
  $('api-config-dialog').addEventListener('close',() => { $('api-config-password').value = ''; });
  $('api-open-config').addEventListener('click',() => { message('api-config-status',''); $('api-config-dialog').showModal(); });
  $('api-open-key').addEventListener('click',() => { clearSecrets(); $('api-key-title').textContent=t('创建 API 密钥'); message('api-key-form-status',''); $('api-key-dialog').showModal(); });
  $('api-open-channel').addEventListener('click',() => { message('api-channel-status',''); $('api-channel-dialog').showModal(); });
  $('api-show-example').addEventListener('click',() => {
    $('api-example').textContent = `curl '${baseUrl}/chat/completions' \\\n  -H 'Authorization: Bearer YOUR_API_KEY' \\\n  -H 'Content-Type: application/json' \\\n  -d '${JSON.stringify({model:engine.models?.[0] || 'YOUR_MODEL',messages:[{role:'user',content:'Hello'}],stream:true})}'`;
    $('api-example-dialog').showModal();
  });
  $('api-key-search').addEventListener('input',renderKeys);
  $('api-key-filter').addEventListener('change',renderKeys);
  $('api-user-search').addEventListener('input',renderAdminUsage);
  $('api-refresh').addEventListener('click',async () => { const button = $('api-refresh'); button.disabled = true; message('api-key-status',''); try { await Promise.all([load(),isAdmin ? loadAdmin() : null]); } catch(error) { message('api-key-status',error.message); } finally { button.disabled = false; } });
  $('api-key-form').addEventListener('submit',async event => {
    event.preventDefault();
    const version = keyDialogVersion;
    const button = event.submitter || $('api-key-form').querySelector('[type="submit"]'); button.disabled = true;
    message('api-key-form-status','');
    try {
      const data = await send('/api/api2api/keys','POST',{name:$('api-key-name').value});
      if (version !== keyDialogVersion || !$('api-key-dialog').open) {
        try { await api(`/api/api2api/keys/${encodeURIComponent(data.item.id)}`,{method:'DELETE'}); }
        catch { message('api-key-status','密钥创建期间窗口已关闭，请在密钥列表撤销该密钥。'); }
        await load();
        return;
      }
      $('new-api-key').textContent = data.item.api_key;
      $('api-key-form').hidden = true;
      $('api-key-result').hidden = false;
      tab('keys');
      await load();
      if (isAdmin) await loadAdmin();
    } catch(error) { message('api-key-form-status',error.message); }
    finally { button.disabled = false; }
  });
  async function keyAction(event) {
    const button=event.target.closest('button');
    if(!button || button.disabled)return;
    if(button.dataset.moreKey){
      const row=keys.find(row=>row.id===button.dataset.moreKey);if(!row)return;
      revokeKey=row.id;
      $('api-menu-copy').hidden=!row.can_copy;
      $('api-menu-copy').dataset.copyKey=row.id;
      $('api-menu-revoke').hidden=!(row.revocable || row.revoke_pending);
      const menu=$('api-key-menu'),rect=button.getBoundingClientRect();
      menu.style.left=`${Math.max(8,Math.min(rect.right-180,window.innerWidth-188))}px`;
      menu.style.top=`${Math.max(8,Math.min(rect.bottom+6,window.innerHeight-100))}px`;
      menu.showModal();return;
    }
    if(button.dataset.editKey){
      limitsKey=keys.find(row=>row.id===button.dataset.editKey);if(!limitsKey)return;
      limitsVersion++;
      $('api-limits-name').textContent=limitsKey.name;
      $('api-limits-models').value=limitsKey.model_limits || '';
      $('api-limits-never').checked=limitsKey.expired_at===-1;
      const expires=new Date(limitsKey.expired_at*1000);
      $('api-limits-expires').value=limitsKey.expired_at>0?new Date(expires.getTime()-expires.getTimezoneOffset()*60000).toISOString().slice(0,16):'';
      $('api-limits-date-field').hidden=$('api-limits-never').checked;
      $('api-limits-expires').required=!$('api-limits-never').checked;
      message('api-limits-status','');$('api-limits-dialog').showModal();return;
    }
    if(!button.dataset.copyKey && !button.dataset.toggleKey)return;
    if($('api-key-menu').open)$('api-key-menu').close();
    button.disabled=true;
    try{
      if(button.dataset.copyKey){
        const version=keyDialogVersion;
        const data=await send(`/api/api2api/keys/${encodeURIComponent(button.dataset.copyKey)}/reveal`,'POST',{});
        if(version!==keyDialogVersion || !$('view-api2api').isConnected || location.hash!=='#view-api2api')return;
        try{await navigator.clipboard.writeText(data.api_key);message('api-key-status','密钥已复制',true);}
        catch{clearSecrets();$('api-key-title').textContent=t('复制 API 密钥');$('new-api-key').textContent=data.api_key;$('api-key-form').hidden=true;$('api-key-result').hidden=false;$('api-key-dialog').showModal();}
      }
      if(button.dataset.toggleKey){
        const enabled=button.dataset.keyEnabled==='true';
        await send(`/api/api2api/keys/${encodeURIComponent(button.dataset.toggleKey)}/enabled`,'PUT',{enabled});
        message('api-key-status',enabled?'密钥已启用':'密钥已停用，可随时重新启用',true);
        await load();
      }
    }catch(error){message('api-key-status',error.message);if(button.dataset.toggleKey)await load();}
    finally{button.disabled=false;}
  }
  $('api-key-list').addEventListener('click',keyAction);
  $('api-key-menu').addEventListener('click',event=>{
    if(event.target===$('api-key-menu')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom)event.target.close();return;}
    keyAction(event);
  });
  $('api-menu-revoke').addEventListener('click',()=>{
    $('api-key-menu').close();$('api-revoke-name').textContent=keys.find(row=>row.id===revokeKey)?.name || '';
    message('api-revoke-status','');$('api-revoke-dialog').showModal();
  });
  $('api-limits-never').addEventListener('change',()=>{
    $('api-limits-date-field').hidden=$('api-limits-never').checked;
    $('api-limits-expires').required=!$('api-limits-never').checked;
  });
  $('api-limits-dialog').addEventListener('close',()=>{limitsVersion++;limitsKey=null;$('api-limits-form').reset();});
  $('api-limits-form').addEventListener('submit',async event=>{
    event.preventDefault();if(!limitsKey)return;
    const key=limitsKey,version=limitsVersion,button=event.submitter || $('api-limits-form').querySelector('[type="submit"]');button.disabled=true;
    const models=$('api-limits-models').value.split(/[,\n]/).map(value=>value.trim()).filter(Boolean);
    const expired_at=$('api-limits-never').checked?-1:Math.floor(new Date($('api-limits-expires').value).getTime()/1000);
    try{
      await send(`/api/api2api/keys/${encodeURIComponent(key.id)}/limits`,'PUT',{models,expired_at,revision:key.limits_revision});
      if(version===limitsVersion){$('api-limits-dialog').close();message('api-key-status','密钥限制已更新',true);}
      await load();
    }catch(error){if(version===limitsVersion)message('api-limits-status',error.message);}
    finally{button.disabled=false;}
  });
  $('api-revoke-form').addEventListener('submit',async event => {
    event.preventDefault(); const button = event.submitter || $('api-revoke-form').querySelector('[type="submit"]'); button.disabled = true;
    try { await api(`/api/api2api/keys/${encodeURIComponent(revokeKey)}`,{method:'DELETE'}); $('api-revoke-dialog').close(); message('api-key-status','密钥已撤销',true); }
    catch(error) { message('api-revoke-status',error.message); }
    finally { button.disabled = false; await load(); }
  });
  $('api-config-form').addEventListener('submit',async event => {
    event.preventDefault(); const button = event.submitter || $('api-config-form').querySelector('[type="submit"]'); button.disabled = true;
    try {
      await send('/api/admin/api2api/config','PUT',{engine:'newapi',base_url:$('api-config-url').value,management_username:$('api-config-username').value,management_password:$('api-config-password').value,initial_quota:Number($('api-config-quota').value),enabled:$('api-config-enabled').checked,model_allowlist:$('api-config-models').value.split(',').map(value => value.trim()).filter(Boolean)});
      $('api-config-dialog').close();
      message('api-key-status','配置已保存',true);
      await Promise.all([load(),loadAdmin()]);
    } catch(error) { message('api-config-status',error.message); }
    finally { $('api-config-password').value = ''; button.disabled = false; }
  });
  $('api-verify').addEventListener('click',async () => { const button = $('api-verify'); button.disabled = true; try { await send('/api/admin/api2api/verify','POST',{}); message('api-config-status','网关连接正常',true); } catch(error) { message('api-config-status',error.message); } finally { button.disabled = false; } });
  $('api-channel-form').addEventListener('submit',async event => {
    event.preventDefault(); const button = event.submitter || $('api-channel-form').querySelector('[type="submit"]'); button.disabled = true;
    try { await send('/api/admin/api2api/channels','POST',{name:$('api-channel-name').value,base_url:$('api-channel-url').value,api_key:$('api-channel-key').value,models:$('api-channel-models').value.split(',').map(value => value.trim()).filter(Boolean)}); $('api-channel-dialog').close(); await Promise.all([loadChannels(),load()]); }
    catch(error) { message('api-channel-status',error.message); }
    finally { button.disabled = false; }
  });
  $('admin-api-usage-rows').addEventListener('click',event => { const button = event.target.closest('[data-quota-user]'); if (!button) return; quotaUser = button.dataset.quotaUser; $('api-quota-user').textContent = button.dataset.quotaName; $('api-quota-form').reset(); message('api-user-quota-status',''); $('api-quota-dialog').showModal(); });
  $('api-quota-form').addEventListener('submit',async event => {
    event.preventDefault(); const button = event.submitter || $('api-quota-form').querySelector('[type="submit"]'); button.disabled = true;
    try { await send(`/api/admin/api2api/users/${encodeURIComponent(quotaUser)}/quota`,'PUT',{quota:Number($('api-user-quota').value)}); $('api-quota-dialog').close(); message('api-key-status','剩余额度已更新',true); await load(); }
    catch(error) { message('api-user-quota-status',error.message); }
    finally { button.disabled = false; }
  });
  window.addEventListener('hashchange',() => { if (location.hash !== '#view-api2api') { document.querySelectorAll('#view-api2api dialog[open]').forEach(node => node.close()); clearSecrets(); } });
  window.addEventListener('pagehide',clearSecrets);
  document.addEventListener('site:language',() => { renderKeys(); renderUsage(); renderEngine(); if(isAdmin)renderAdminUsage(); });
  return {start(admin) { isAdmin = admin; apiRecordsController.start(admin); $('api-admin-tab').hidden = !admin; tab(activeTab); },load,loadAdmin};
})();
