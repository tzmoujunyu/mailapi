const apiController = (() => {
  const $ = id => document.getElementById(id);
  const t = value => window.SiteLanguage?.t(value) || value;
  const esc = value => { const node = document.createElement('span'); node.textContent = value == null ? '' : String(value); return node.innerHTML; };
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
  let isAdmin = false, activeTab = 'overview', keys = [], usage = {items:[]}, engine = {}, quotaUser = null, revokeKey = null, keyDialogVersion = 0;
  function message(id, value, success = false) { $(id).textContent = t(value); $(id).classList.toggle('success',success); }
  function tab(name) {
    if (name === 'settings' && !isAdmin) return;
    activeTab = name;
    document.querySelectorAll('[data-api-tab]').forEach(node => node.setAttribute('aria-pressed',String(node.dataset.apiTab === name)));
    document.querySelectorAll('[data-api-panel]').forEach(node => { node.hidden = node.dataset.apiPanel !== name; });
  }
  function renderKeys() {
    const query = $('api-key-search').value.trim().toLowerCase();
    const filtered = keys.filter(row => `${row.name} ${row.key_prefix}`.toLowerCase().includes(query));
    $('api-key-list').innerHTML = filtered.map(row => `<tr><td><span data-i18n-skip>${esc(row.name)}</span><small data-i18n-skip>${esc(row.key_prefix)}…</small></td><td>${row.provider === 'newapi' ? 'NewAPI' : t('共享模式')}</td><td><span class="${row.enabled ? 'api-good' : 'api-dim'}" data-ui-text>${esc(t(row.enabled ? '可用' : row.available_here === false ? '其他网关 / 模式' : row.revoke_pending ? '待完成撤销' : '已撤销'))}</span></td><td>${row.provider === 'newapi' ? num(row.used_quota) : '—'}</td><td>${date(row.created_at)}</td><td>${date(row.last_used_at)}</td><td>${row.revocable || row.enabled || row.revoke_pending ? `<button type="button" class="api-link api-bad" data-revoke-key="${esc(row.id)}">${esc(t(row.revoke_pending ? '重试撤销' : '撤销'))}</button>` : '—'}</td></tr>`).join('') || `<tr><td colspan="7" class="api-empty" data-ui-text>${esc(t('没有符合条件的密钥。'))}</td></tr>`;
    $('api-active-keys').textContent = num(keys.filter(row => row.enabled).length);
    $('dashboard-keys').textContent = keys.filter(row => row.enabled).length;
  }
  const succeeded = row => row.status_code >= 200 && row.status_code < 300 && !row.error;
  function renderLogs() {
    const query = $('api-log-search').value.trim().toLowerCase(), status = $('api-log-status').value;
    $('api-usage-rows').innerHTML = usage.items.filter(row => (!query || String(row.model).toLowerCase().includes(query)) && (status === 'all' || (status === 'success') === succeeded(row))).map(row => `<tr><td>${date(row.created_at)}</td><td data-i18n-skip>${esc(row.model || '—')}</td><td>${num(row.prompt_tokens)}</td><td>${num(row.completion_tokens)}</td><td>${num(row.total_tokens)}</td><td>${num(row.request_ms)} ms</td><td><span class="${succeeded(row) ? 'api-good' : 'api-bad'}">${esc(row.status_code)}${row.error && row.status_code < 400 ? ' · '+esc(t('流中断')) : ''}</span></td></tr>`).join('') || `<tr><td colspan="7" class="api-empty" data-ui-text>${esc(t('暂无符合条件的调用记录。'))}</td></tr>`;
  }
  function renderUsage() {
    for (const [id, field] of Object.entries({'dashboard-requests':'requests','dashboard-tokens':'total_tokens','dashboard-input':'prompt_tokens','dashboard-output':'completion_tokens','api-metric-requests':'requests','api-metric-tokens':'total_tokens','api-input-total':'prompt_tokens','api-output-total':'completion_tokens'})) $(id).textContent = num(usage[field]);
    const split = usage.prompt_tokens + usage.completion_tokens;
    $('usage-ring').classList.toggle('has-data',split > 0);
    $('usage-ring').style.setProperty('--input-share',`${split ? 100 * usage.prompt_tokens / split : 0}%`);
    $('dashboard-calls').innerHTML = usage.items.slice(0,3).map(row => `<li><span data-i18n-skip>${esc(row.model || '—')}<small>${date(row.created_at)}</small></span><b>${num(row.total_tokens)} Token</b></li>`).join('') || `<li data-ui-text>${esc(t('暂无调用记录'))}</li>`;
    $('api-metric-duration').textContent = usage.items.length ? `${num(Math.round(usage.items.reduce((sum,row) => sum + row.request_ms,0) / usage.items.length))} ms` : '—';
    $('api-usage-summary').textContent = t(`请求 ${usage.requests} 次 · 输入 ${usage.prompt_tokens} · 输出 ${usage.completion_tokens} · 总计 ${usage.total_tokens} Token`);
    renderLogs();
  }
  function renderEngine() {
    const native = engine.engine === 'newapi';
    $('api-engine-badge').textContent = native ? 'NewAPI' : t('共享模式');
    $('api-engine-description').textContent = t(native ? '密钥由 NewAPI 签发，本站管理入口和用户身份。' : '共享模式使用本站密钥，转发时使用管理员配置的上游凭据。');
    $('api-metric-quota').textContent = native && engine.linked ? num(engine.quota) : '—';
    $('api-quota-hint').textContent = t(native ? engine.linked ? 'quota 点 · 以 NewAPI 为准' : '创建密钥后关联独立网关账户' : '当前共享模式不管理额度');
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
  }
  function modeFields() {
    const native = $('api-config-engine').value === 'newapi';
    $('api-native-fields').hidden = !native;
    $('api-legacy-field').hidden = native;
    $('api-config-username').required = native;
    $('api-config-quota').required = native;
    $('api-open-channel').disabled = !native;
    $('api-verify').disabled = !native;
  }
  async function loadChannels() {
    if ($('api-config-engine').value !== 'newapi') { $('api-channels').textContent = t('原共享模式由现有上游提供模型。'); return; }
    try {
      const data = await api('/api/admin/api2api/channels');
      $('api-channels').innerHTML = data.items.map(row => `<div class="api-channel"><div><strong data-i18n-skip>${esc(row.name)}</strong><span class="${row.status===1 ? 'api-good' : 'api-dim'}" data-ui-text>${esc(t(row.status===1 ? '启用' : '停用'))}</span></div><p data-i18n-skip>${esc(row.base_url)}</p><p data-i18n-skip>${esc(row.models)}</p></div>`).join('') || `<p class="api-empty" data-ui-text>${esc(t('暂无渠道，请添加上游连接。'))}</p>`;
    } catch (error) { $('api-channels').textContent = t(error.message); }
  }
  async function loadAdmin() {
    const [config,data] = await Promise.all([api('/api/admin/api2api/config'),api('/api/admin/api2api/usage')]);
    $('api-config-engine').value = config.configured ? config.engine || 'legacy' : 'newapi';
    $('api-config-url').value = config.base_url || '';
    $('api-config-enabled').checked = config.enabled === true;
    $('api-config-models').value = (config.model_allowlist || []).join(',');
    $('api-config-username').value = config.management_username || '';
    $('api-config-quota').value = config.initial_quota || 0;
    modeFields();
    $('admin-api-usage-rows').innerHTML = data.items.map(row => `<tr><td data-i18n-skip>${esc(row.username || row.user_id)}<small>${esc(row.user_note)}</small></td><td>${num(row.requests)}</td><td>${num(row.prompt_tokens)}</td><td>${num(row.completion_tokens)}</td><td>${num(row.total_tokens)}</td><td>${config.engine === 'newapi' ? `<button type="button" class="api-link" data-quota-user="${esc(row.user_id)}" data-quota-name="${esc(row.username || row.user_id)}">${esc(t('调整额度'))}</button>` : '—'}</td></tr>`).join('') || `<tr><td colspan="6" class="api-empty" data-ui-text>${esc(t('创建密钥或发起调用后显示用户。'))}</td></tr>`;
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
    $('api-config-key').value = '';
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
  $('api-open-key').addEventListener('click',() => { clearSecrets(); message('api-key-form-status',''); $('api-key-dialog').showModal(); });
  $('api-open-channel').addEventListener('click',() => { message('api-channel-status',''); $('api-channel-dialog').showModal(); });
  $('api-show-example').addEventListener('click',() => {
    $('api-example').textContent = `curl '${baseUrl}/chat/completions' \\\n  -H 'Authorization: Bearer YOUR_API_KEY' \\\n  -H 'Content-Type: application/json' \\\n  -d '${JSON.stringify({model:engine.models?.[0] || 'YOUR_MODEL',messages:[{role:'user',content:'Hello'}],stream:true})}'`;
    $('api-example-dialog').showModal();
  });
  $('api-key-search').addEventListener('input',renderKeys);
  $('api-log-search').addEventListener('input',renderLogs);
  $('api-log-status').addEventListener('change',renderLogs);
  $('api-config-engine').addEventListener('change',modeFields);
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
  $('api-key-list').addEventListener('click',event => {
    const button = event.target.closest('[data-revoke-key]');
    if (!button) return;
    revokeKey = button.dataset.revokeKey;
    $('api-revoke-name').textContent = keys.find(row => row.id === revokeKey)?.name || '';
    message('api-revoke-status','');
    $('api-revoke-dialog').showModal();
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
      await send('/api/admin/api2api/config','PUT',{engine:$('api-config-engine').value,base_url:$('api-config-url').value,upstream_key:$('api-config-key').value,management_username:$('api-config-username').value,management_password:$('api-config-password').value,initial_quota:Number($('api-config-quota').value),enabled:$('api-config-enabled').checked,model_allowlist:$('api-config-models').value.split(',').map(value => value.trim()).filter(Boolean)});
      message('api-config-status','配置已保存',true);
      await Promise.all([load(),loadAdmin()]);
    } catch(error) { message('api-config-status',error.message); }
    finally { $('api-config-password').value = ''; $('api-config-key').value = ''; button.disabled = false; }
  });
  $('api-verify').addEventListener('click',async () => { const button = $('api-verify'); button.disabled = true; try { await send('/api/admin/api2api/verify','POST',{}); message('api-config-status','NewAPI 连接正常',true); } catch(error) { message('api-config-status',error.message); } finally { button.disabled = false; } });
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
  document.addEventListener('site:language',() => { renderKeys(); renderUsage(); renderEngine(); });
  return {start(admin) { isAdmin = admin; $('api-admin-tab').hidden = !admin; tab(activeTab); },load,loadAdmin};
})();
