(() => {
  const $ = id => document.getElementById(id);
  const escapeHtml = value => { const node = document.createElement('div'); node.textContent = value == null ? '' : String(value); return node.innerHTML; };
  const api = async (url, options = {}) => {
    const response = await fetch(url, { cache: 'no-store', ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error?.message || `请求失败（${response.status}）`);
    return data;
  };
  const money = value => `${Number(value || 0).toFixed(2)} 元`;
  const statusText = { pending: '待审核', approved: '已通过', rejected: '已驳回' };
  let isAdmin = false;
  let adminApplications = [];

  function setError(id, message) { $(id).textContent = message || ''; }
  function renderKeys(items) {
    $('api-key-list').innerHTML = items.length ? items.map(item => `<div class="row"><span>${escapeHtml(item.name)} <span class="tag">${escapeHtml(item.key_prefix)}…</span></span><button type="button" data-revoke-key="${escapeHtml(item.id)}">撤销</button></div>`).join('') : '<p class="muted">还没有 API Key。</p>';
  }
  async function loadApi2Api() {
    const [keys, usage] = await Promise.all([api('/api/api2api/keys'), api('/api/api2api/usage')]);
    renderKeys(keys.items);
    const baseUrl = `${location.origin}/api2api/v1`;
    $('api2api-base-url').textContent = baseUrl;
    $('overview-base-url').textContent = baseUrl;
    $('api-usage-summary').textContent = `请求 ${usage.requests} 次 · 输入 ${usage.prompt_tokens} · 输出 ${usage.completion_tokens} · 总计 ${usage.total_tokens} Token`;
    $('api-usage-rows').innerHTML = usage.items.map(item => `<tr><td>${escapeHtml(item.created_at)}</td><td>${escapeHtml(item.model || '-')}</td><td>${item.total_tokens}</td><td>${item.status_code}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">暂无调用记录。</td></tr>';
    return { keys, usage };
  }
  async function loadAdminApi2Api() {
    const [config, usage] = await Promise.all([api('/api/admin/api2api/config'), api('/api/admin/api2api/usage')]);
    $('api-config-url').value = config.base_url || '';
    $('api-config-enabled').checked = config.enabled === true;
    $('api-config-models').value = (config.model_allowlist || []).join(',');
    $('admin-api-usage-rows').innerHTML = usage.items.map(item => `<tr><td>${escapeHtml(item.user_id)}</td><td>${item.requests}</td><td>${item.prompt_tokens}</td><td>${item.completion_tokens}</td><td>${item.total_tokens}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">暂无全站调用记录。</td></tr>';
  }
  function renderAnnouncements(items) {
    $('announcements').innerHTML = items.map(item => `<article class="notice ${item.pinned ? 'pinned' : ''}"><div class="row"><strong>${escapeHtml(item.title)}</strong>${item.pinned ? '<span class="tag">置顶</span>' : ''}</div><p>${escapeHtml(item.body).replace(/\n/g, '<br>')}</p><small class="muted">${escapeHtml(item.updated_at)}</small></article>`).join('') || '<p class="muted">暂无公告。</p>';
  }
  function renderFinance(items, target, admin = false) {
    target.innerHTML = items.map(item => {
      const groups = (item.groups || []).join('、') || '未分组';
      const files = (item.files || []).map(file => admin ? `<a class="btn" href="/api/finance/applications/${encodeURIComponent(item.id)}/download?kind=${encodeURIComponent(file.kind)}">${file.kind === 'invoice' ? '发票' : '支付记录'}</a>` : escapeHtml(file.kind === 'invoice' ? '发票' : '支付记录')).join(' ');
      if (!admin) return `<tr><td>${escapeHtml(item.created_at)}</td><td>${escapeHtml(groups)}</td><td>${escapeHtml(item.project)}</td><td>${money(item.amount)}</td><td>${escapeHtml(statusText[item.status] || item.status)}</td><td>${files} <a class="btn" href="/api/finance/applications/${encodeURIComponent(item.id)}/download.zip">下载压缩包</a></td></tr>`;
      return `<tr><td><input type="checkbox" data-finance-select="${escapeHtml(item.id)}"></td><td>${escapeHtml(item.username)}${item.user_note ? `（${escapeHtml(item.user_note)}）` : ''}<br><span class="muted">${escapeHtml(groups)}</span></td><td>${escapeHtml(item.project)}</td><td>${money(item.amount)}</td><td>${escapeHtml(statusText[item.status] || item.status)}</td><td><div class="actions">${files}<a class="btn" href="/api/finance/applications/${encodeURIComponent(item.id)}/download.zip">压缩包</a><button type="button" data-review="${escapeHtml(item.id)}" data-status="approved">通过</button><button type="button" data-review="${escapeHtml(item.id)}" data-status="rejected">驳回</button></div></td></tr>`;
    }).join('') || `<tr><td colspan="${admin ? 6 : 6}" class="muted">暂无报销申请。</td></tr>`;
  }
  async function loadFinance() {
    const [announcements, applications] = await Promise.all([api('/api/finance/announcements'), api('/api/finance/applications')]);
    renderAnnouncements(announcements.items);
    renderFinance(applications.items, $('finance-rows'));
    if (isAdmin) {
      const adminData = await api('/api/admin/finance/applications');
      adminApplications = adminData.items;
      renderFinance(adminApplications, $('admin-finance-rows'), true);
    }
    return { announcements, applications: isAdmin ? adminApplications : applications.items };
  }
  function updateDashboard(apiData, financeItems) {
    if (!apiData) return;
    $('dashboard-requests').textContent = apiData.usage.requests;
    $('dashboard-tokens').textContent = Number(apiData.usage.total_tokens || 0).toLocaleString('zh-CN');
    $('dashboard-keys').textContent = apiData.keys.items.length;
    $('dashboard-pending').textContent = financeItems.filter(item => item.status === 'pending').length;
  }
  $('api-key-form').addEventListener('submit', async event => {
    event.preventDefault(); setError('api-key-status', '');
    try { const data = await api('/api/api2api/keys', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: $('api-key-name').value}) }); $('new-api-key').textContent = `新 Key（只显示一次）：${data.item.api_key}`; $('new-api-key').classList.remove('hidden'); $('api-key-form').reset(); await loadApi2Api(); } catch (error) { setError('api-key-status', error.message); }
  });
  $('api-key-list').addEventListener('click', async event => { const button = event.target.closest('[data-revoke-key]'); if (!button) return; if (!confirm('撤销此 API Key？')) return; try { await api(`/api/api2api/keys/${encodeURIComponent(button.dataset.revokeKey)}`, {method: 'DELETE'}); await loadApi2Api(); } catch (error) { setError('api-key-status', error.message); } });
  $('api-config-form').addEventListener('submit', async event => { event.preventDefault(); setError('api-config-status', ''); try { await api('/api/admin/api2api/config', {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({base_url: $('api-config-url').value, upstream_key: $('api-config-key').value, enabled: $('api-config-enabled').checked, model_allowlist: $('api-config-models').value.split(',').map(item => item.trim()).filter(Boolean)})}); $('api-config-key').value = ''; $('api-config-status').textContent = '已保存。'; await loadAdminApi2Api(); } catch (error) { setError('api-config-status', error.message); } });
  $('finance-form').addEventListener('submit', async event => { event.preventDefault(); setError('finance-status', ''); const form = new FormData(); form.set('project', $('finance-project').value); form.set('amount', $('finance-amount').value); form.set('invoice', $('finance-invoice').files[0]); if ($('finance-payment').files[0]) form.set('payment_record', $('finance-payment').files[0]); try { await api('/api/finance/applications', {method:'POST', body: form}); $('finance-form').reset(); $('finance-status').textContent = '申请已提交。'; await loadFinance(); } catch (error) { setError('finance-status', error.message); } });
  $('announcement-form').addEventListener('submit', async event => { event.preventDefault(); setError('announcement-status', ''); try { await api('/api/admin/finance/announcements', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({title:$('announcement-title').value, body:$('announcement-body').value, pinned:$('announcement-pinned').checked})}); $('announcement-form').reset(); $('announcement-status').textContent = '公告已发布。'; await loadFinance(); } catch (error) { setError('announcement-status', error.message); } });
  $('finance-batch-download').addEventListener('click', async () => { const ids = [...document.querySelectorAll('[data-finance-select]:checked')].map(input => input.dataset.financeSelect); if (!ids.length) return setError('announcement-status', '请先选择申请'); const response = await fetch('/api/admin/finance/download.zip', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({application_ids: ids})}); if (!response.ok) return setError('announcement-status', '批量下载失败'); const blob = await response.blob(); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = `${new Date().toISOString().slice(0,10).replaceAll('-','')}报销申请.zip`; link.click(); URL.revokeObjectURL(link.href); });
  $('admin-finance-rows').addEventListener('click', async event => { const button = event.target.closest('[data-review]'); if (!button) return; const note = prompt(button.dataset.status === 'approved' ? '审核备注（可选）' : '驳回原因（可选）') || ''; try { await api(`/api/admin/finance/applications/${encodeURIComponent(button.dataset.review)}/review`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status:button.dataset.status, note})}); await loadFinance(); } catch (error) { setError('announcement-status', error.message); } });
  api('/api/test-features/status').then(status => { isAdmin = ['admin','manager'].includes(status.role); $('api2api-admin').classList.toggle('hidden', !isAdmin); $('finance-admin').classList.toggle('hidden', !isAdmin); return Promise.all([loadApi2Api(), loadFinance(), isAdmin ? loadAdminApi2Api() : null]); }).then(([apiData, financeData]) => updateDashboard(apiData, financeData.applications)).catch(error => { setError('api-key-status', error.message); });
})();
