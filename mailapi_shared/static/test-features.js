(() => {
  const $ = id => document.getElementById(id);
  const escapeHtml = value => {
    const node = document.createElement('div');
    node.textContent = value == null ? '' : String(value);
    return node.innerHTML;
  };
  const api = async (url, options = {}) => {
    const response = await fetch(url, { cache: 'no-store', ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error?.message || `请求失败（${response.status}）`);
    return data;
  };
  let isAdmin = false;
  const loadFinance = () => financeController.load();

  function setError(id, message) {
    const target = $(id);
    if (target) target.textContent = message || '';
  }

  function renderKeys(items) {
    $('api-key-list').innerHTML = items.length
      ? items.map(item => `<div class="row"><span>${escapeHtml(item.name)} <span class="tag">${escapeHtml(item.key_prefix)}…</span></span>${item.enabled ? `<button type="button" data-revoke-key="${escapeHtml(item.id)}">撤销</button>` : '<span class="tag" data-ui-text>已撤销</span>'}</div>`).join('')
      : '<p class="muted" data-ui-text>还没有 API Key。</p>';
  }

  const baseUrl = `${location.origin}/api2api/v1`;
  $('api2api-base-url').textContent = baseUrl;
  $('copy-base-url').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(baseUrl);
      $('copy-url-status').textContent = '已复制';
    } catch {
      $('copy-url-status').textContent = '请选中地址手动复制';
    }
  });

  async function loadApi2Api() {
    const [keys, usage] = await Promise.all([api('/api/api2api/keys'), api('/api/api2api/usage')]);
    renderKeys(keys.items);
    $('dashboard-keys').textContent = keys.items.filter(item => item.enabled).length;
    $('dashboard-requests').textContent = usage.requests.toLocaleString('zh-CN');
    $('dashboard-tokens').textContent = usage.total_tokens.toLocaleString('zh-CN');
    $('dashboard-input').textContent = usage.prompt_tokens.toLocaleString('zh-CN');
    $('dashboard-output').textContent = usage.completion_tokens.toLocaleString('zh-CN');
    const splitTotal = usage.prompt_tokens + usage.completion_tokens;
    const ring = $('usage-ring');
    ring.classList.toggle('has-data', splitTotal > 0);
    ring.style.setProperty('--input-share', `${splitTotal ? 100 * usage.prompt_tokens / splitTotal : 0}%`);
    $('dashboard-calls').innerHTML = usage.items.slice(0, 3).map(item => `<li><span>${escapeHtml(item.model || '未知模型')}<small>${escapeHtml(item.created_at)}</small></span><b>${item.total_tokens.toLocaleString('zh-CN')} Token</b></li>`).join('') || '<li data-ui-text>暂无调用记录</li>';
    $('api-usage-summary').textContent = `请求 ${usage.requests} 次 · 输入 ${usage.prompt_tokens} · 输出 ${usage.completion_tokens} · 总计 ${usage.total_tokens} Token`;
    $('api-usage-rows').innerHTML = usage.items.map(item => `<tr><td>${escapeHtml(item.created_at)}</td><td>${escapeHtml(item.model || '-')}</td><td>${item.total_tokens}</td><td>${item.status_code}</td></tr>`).join('')
      || '<tr><td colspan="4" class="muted" data-ui-text>暂无调用记录。</td></tr>';
  }

  async function loadAdminApi2Api() {
    const [config, usage] = await Promise.all([api('/api/admin/api2api/config'), api('/api/admin/api2api/usage')]);
    $('api-config-url').value = config.base_url || '';
    $('api-config-enabled').checked = config.enabled === true;
    $('api-config-models').value = (config.model_allowlist || []).join(',');
    $('admin-api-usage-rows').innerHTML = usage.items.map(item => `<tr><td>${escapeHtml(item.user_id)}</td><td>${item.requests}</td><td>${item.prompt_tokens}</td><td>${item.completion_tokens}</td><td>${item.total_tokens}</td></tr>`).join('')
      || '<tr><td colspan="5" class="muted" data-ui-text>暂无全站调用记录。</td></tr>';
  }

  $('api-key-form').addEventListener('submit', async event => {
    event.preventDefault();
    setError('api-key-status', '');
    try {
      const data = await api('/api/api2api/keys', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: $('api-key-name').value}),
      });
      $('new-api-key').textContent = `新 Key（只显示一次）：${data.item.api_key}`;
      $('new-api-key').classList.remove('hidden');
      $('api-key-form').reset();
      await loadApi2Api();
    } catch (error) {
      setError('api-key-status', error.message);
    }
  });

  $('api-key-list').addEventListener('click', async event => {
    const button = event.target.closest('[data-revoke-key]');
    if (!button) return;
    if (!confirm('撤销此 API Key？')) return;
    try {
      await api(`/api/api2api/keys/${encodeURIComponent(button.dataset.revokeKey)}`, {method: 'DELETE'});
      await loadApi2Api();
    } catch (error) {
      setError('api-key-status', error.message);
    }
  });

  $('api-config-form').addEventListener('submit', async event => {
    event.preventDefault();
    setError('api-config-status', '');
    try {
      await api('/api/admin/api2api/config', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          base_url: $('api-config-url').value,
          upstream_key: $('api-config-key').value,
          enabled: $('api-config-enabled').checked,
          model_allowlist: $('api-config-models').value.split(',').map(item => item.trim()).filter(Boolean),
        }),
      });
      $('api-config-key').value = '';
      $('api-config-status').textContent = '已保存。';
      await loadAdminApi2Api();
    } catch (error) {
      setError('api-config-status', error.message);
    }
  });

  document.addEventListener('test:view-enter', () => {
    loadApi2Api().catch(error => setError('api-key-status', error.message));
    loadFinance().catch(error => setError('finance-status', error.message));
    if (isAdmin) loadAdminApi2Api().catch(error => setError('api-key-status', error.message));
  });

  api('/api/test-features/status')
    .then(status => {
      isAdmin = ['admin', 'manager'].includes(status.role);
      $('api2api-admin').classList.toggle('hidden', !isAdmin);
      financeController.start(isAdmin);
      return Promise.all([
        loadApi2Api().catch(error => {
          $('dashboard-calls').textContent = '调用记录加载失败';
          setError('api-key-status', error.message);
        }),
        loadFinance().catch(error => {
          $('dashboard-finance').textContent = '报销记录加载失败';
          setError('finance-status', error.message);
        }),
        isAdmin ? loadAdminApi2Api() : null,
      ]);
    })
    .catch(error => setError('api-key-status', error.message));
})();
