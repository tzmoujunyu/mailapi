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
  const money = value => `${Number(value || 0).toFixed(2)} 元`;
  const statusText = { pending: '待审核', approved: '已通过', rejected: '已驳回', cancelled: '已撤销' };
  const fileLabel = { invoice: '发票', payment: '支付记录' };
  let isAdmin = false;
  let adminApplications = [];
  let adminAnnouncements = [];
  let announcementEditId = '';

  function setError(id, message) {
    const target = $(id);
    if (target) target.textContent = message || '';
  }

  function renderKeys(items) {
    $('api-key-list').innerHTML = items.length
      ? items.map(item => `<div class="row"><span>${escapeHtml(item.name)} <span class="tag">${escapeHtml(item.key_prefix)}…</span></span>${item.enabled ? `<button type="button" data-revoke-key="${escapeHtml(item.id)}">撤销</button>` : '<span class="tag">已撤销</span>'}</div>`).join('')
      : '<p class="muted">还没有 API Key。</p>';
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
    $('dashboard-calls').innerHTML = usage.items.slice(0, 3).map(item => `<li><span>${escapeHtml(item.model || '未知模型')}<small>${escapeHtml(item.created_at)}</small></span><b>${item.total_tokens.toLocaleString('zh-CN')} Token</b></li>`).join('') || '<li>暂无调用记录</li>';
    $('api-usage-summary').textContent = `请求 ${usage.requests} 次 · 输入 ${usage.prompt_tokens} · 输出 ${usage.completion_tokens} · 总计 ${usage.total_tokens} Token`;
    $('api-usage-rows').innerHTML = usage.items.map(item => `<tr><td>${escapeHtml(item.created_at)}</td><td>${escapeHtml(item.model || '-')}</td><td>${item.total_tokens}</td><td>${item.status_code}</td></tr>`).join('')
      || '<tr><td colspan="4" class="muted">暂无调用记录。</td></tr>';
  }

  async function loadAdminApi2Api() {
    const [config, usage] = await Promise.all([api('/api/admin/api2api/config'), api('/api/admin/api2api/usage')]);
    $('api-config-url').value = config.base_url || '';
    $('api-config-enabled').checked = config.enabled === true;
    $('api-config-models').value = (config.model_allowlist || []).join(',');
    $('admin-api-usage-rows').innerHTML = usage.items.map(item => `<tr><td>${escapeHtml(item.user_id)}</td><td>${item.requests}</td><td>${item.prompt_tokens}</td><td>${item.completion_tokens}</td><td>${item.total_tokens}</td></tr>`).join('')
      || '<tr><td colspan="5" class="muted">暂无全站调用记录。</td></tr>';
  }

  function renderAnnouncements(items) {
    $('announcements').innerHTML = items.map(item => `<article class="notice ${item.pinned ? 'pinned' : ''}"><div class="row"><strong>${escapeHtml(item.title)}</strong>${item.pinned ? '<span class="tag">置顶</span>' : ''}</div><p>${escapeHtml(item.body).replace(/\n/g, '<br>')}</p><small class="muted">${escapeHtml(item.updated_at)}</small></article>`).join('')
      || '<p class="muted">暂无公告。</p>';
  }

  function renderAdminAnnouncements(items) {
    $('admin-announcements').innerHTML = items.map(item => `<article class="announcement-admin-item"><div><div class="row"><strong>${escapeHtml(item.title)}</strong>${item.pinned ? '<span class="tag">置顶</span>' : '<span class="tag">普通</span>'}</div><p>${escapeHtml(item.body)}</p><small class="muted">更新于 ${escapeHtml(item.updated_at)}</small></div><div class="actions"><button type="button" data-edit-announcement="${escapeHtml(item.id)}">编辑</button><button type="button" data-delete-announcement="${escapeHtml(item.id)}">删除</button></div></article>`).join('')
      || '<p class="muted">暂无公告。</p>';
  }

  function statusPill(status) {
    return `<span class="status-pill status-${escapeHtml(status)}">${escapeHtml(statusText[status] || status)}</span>`;
  }

  function renderFinance(items, target, admin = false) {
    target.innerHTML = items.map(item => {
      const groups = (item.groups || []).join('、') || '未分组';
      const files = (item.files || []).map(file => {
        const label = fileLabel[file.kind] || file.kind;
        return `<a class="btn" href="/api/finance/applications/${encodeURIComponent(item.id)}/download?kind=${encodeURIComponent(file.kind)}">${escapeHtml(label)}</a>`;
      }).join(' ');
      const archive = `<a class="btn" href="/api/finance/applications/${encodeURIComponent(item.id)}/download.zip">压缩包</a>`;
      if (!admin) {
        const actions = item.status === 'pending'
          ? `<button type="button" data-edit-finance="${escapeHtml(item.id)}">编辑</button><button type="button" data-cancel-finance="${escapeHtml(item.id)}">撤销</button>`
          : '';
        return `<tr><td>${escapeHtml(item.created_at)}</td><td>${escapeHtml(groups)}</td><td>${escapeHtml(item.project)}</td><td>${money(item.amount)}</td><td>${statusPill(item.status)}</td><td><div class="actions">${files || '<span class="muted">无文件</span>'} ${archive} ${actions}</div></td></tr>`;
      }
      const reviewActions = item.status === 'pending'
        ? '<button type="button" data-review-status="approved">通过</button><button type="button" data-review-status="rejected">驳回</button>'
        : '';
      return `<tr><td><input type="checkbox" data-finance-select="${escapeHtml(item.id)}"></td><td>${escapeHtml(item.username)}${item.user_note ? `（${escapeHtml(item.user_note)}）` : ''}<br><span class="muted">${escapeHtml(groups)}</span></td><td>${escapeHtml(item.project)}</td><td>${money(item.amount)}</td><td>${statusPill(item.status)}</td><td><div class="actions">${files} ${archive} ${reviewActions}</div></td></tr>`;
    }).join('') || `<tr><td colspan="${admin ? 6 : 6}" class="muted">暂无报销申请。</td></tr>`;
  }

  async function loadFinance() {
    const [announcements, applications] = await Promise.all([
      api('/api/finance/announcements'),
      api('/api/finance/applications'),
    ]);
    renderAnnouncements(announcements.items);
    renderFinance(applications.items, $('finance-rows'));
    const mine = applications.items;
    const pending = mine.filter(item => item.status === 'pending').length;
    $('dashboard-pending').textContent = $('finance-pending').textContent = pending;
    $('finance-total').textContent = mine.length;
    const approvedCents = mine.filter(item => item.status === 'approved')
      .reduce((sum, item) => sum + Math.round(Number(item.amount) * 100), 0);
    $('finance-approved').textContent = money(approvedCents / 100);
    $('dashboard-finance').innerHTML = mine.slice(0, 3).map(item => `<li><span>${escapeHtml(item.project)}<small>${money(item.amount)}</small></span>${statusPill(item.status)}</li>`).join('') || '<li>暂无报销申请</li>';
    if (isAdmin) {
      const [adminData, adminAnnouncementData] = await Promise.all([
        api('/api/admin/finance/applications'),
        api('/api/admin/finance/announcements'),
      ]);
      adminApplications = adminData.items;
      adminAnnouncements = adminAnnouncementData.items;
      $('dashboard-review-link').classList.remove('hidden');
      $('dashboard-review').textContent = adminApplications.filter(item => item.status === 'pending').length;
      renderFinance(adminApplications, $('admin-finance-rows'), true);
      renderAdminAnnouncements(adminAnnouncements);
    }
  }

  function openFinanceForm(item = null) {
    const editing = Boolean(item);
    $('finance-edit-id').value = editing ? item.id : '';
    $('finance-project').value = editing ? item.project : '';
    $('finance-amount').value = editing ? item.amount : '';
    $('finance-invoice').value = '';
    $('finance-payment').value = '';
    $('finance-invoice').required = !editing;
    $('finance-dialog-title').textContent = editing ? '编辑报销申请' : '提交报销申请';
    $('finance-submit').textContent = editing ? '保存修改' : '提交申请';
    $('finance-file-hint').textContent = editing
      ? '支持 PDF、JPG、PNG、WebP，单个文件不超过 10 MB。不选择文件则保留原文件。'
      : '支持 PDF、JPG、PNG、WebP，单个文件不超过 10 MB。';
    setError('finance-status', '');
    $('finance-dialog').classList.remove('hidden');
    $('finance-project').focus();
  }

  function closeFinanceForm() {
    $('finance-dialog').classList.add('hidden');
    $('finance-form').reset();
    $('finance-edit-id').value = '';
    $('finance-invoice').required = true;
    $('finance-dialog-title').textContent = '提交报销申请';
    $('finance-submit').textContent = '提交申请';
  }

  function resetAnnouncementForm() {
    announcementEditId = '';
    $('announcement-id').value = '';
    $('announcement-form').reset();
    $('announcement-form-title').textContent = '新增公告';
    $('announcement-submit').textContent = '发布公告';
    $('announcement-cancel-edit').classList.add('hidden');
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

  $('finance-open-form').addEventListener('click', () => openFinanceForm());
  $('finance-close-form').addEventListener('click', closeFinanceForm);
  $('finance-cancel-form').addEventListener('click', closeFinanceForm);
  $('finance-dialog').addEventListener('click', event => {
    if (event.target === $('finance-dialog')) closeFinanceForm();
  });

  $('finance-form').addEventListener('submit', async event => {
    event.preventDefault();
    setError('finance-status', '');
    const editId = $('finance-edit-id').value;
    const form = new FormData();
    form.set('project', $('finance-project').value);
    form.set('amount', $('finance-amount').value);
    if ($('finance-invoice').files[0]) form.set('invoice', $('finance-invoice').files[0]);
    if ($('finance-payment').files[0]) form.set('payment_record', $('finance-payment').files[0]);
    try {
      const endpoint = editId ? `/api/finance/applications/${encodeURIComponent(editId)}` : '/api/finance/applications';
      await api(endpoint, {method: editId ? 'PUT' : 'POST', body: form});
      closeFinanceForm();
      $('finance-status').textContent = editId ? '申请已更新。' : '申请已提交。';
      await loadFinance();
    } catch (error) {
      setError('finance-status', error.message);
    }
  });

  $('finance-rows').addEventListener('click', async event => {
    const editButton = event.target.closest('[data-edit-finance]');
    if (editButton) {
      const item = (await api('/api/finance/applications')).items.find(value => value.id === editButton.dataset.editFinance);
      if (item) openFinanceForm(item);
      return;
    }
    const cancelButton = event.target.closest('[data-cancel-finance]');
    if (!cancelButton) return;
    if (!confirm('撤销这条待审核申请？')) return;
    try {
      await api(`/api/finance/applications/${encodeURIComponent(cancelButton.dataset.cancelFinance)}/cancel`, {method: 'POST'});
      await loadFinance();
    } catch (error) {
      setError('finance-status', error.message);
    }
  });

  $('announcement-form').addEventListener('submit', async event => {
    event.preventDefault();
    setError('announcement-status', '');
    const editing = Boolean(announcementEditId);
    const payload = {
      title: $('announcement-title').value,
      body: $('announcement-body').value,
      pinned: $('announcement-pinned').checked,
    };
    try {
      const endpoint = announcementEditId
        ? `/api/admin/finance/announcements/${encodeURIComponent(announcementEditId)}`
        : '/api/admin/finance/announcements';
      await api(endpoint, {
        method: editing ? 'PUT' : 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      resetAnnouncementForm();
      $('announcement-status').textContent = editing ? '公告已更新。' : '公告已发布。';
      await loadFinance();
    } catch (error) {
      setError('announcement-status', error.message);
    }
  });

  $('announcement-cancel-edit').addEventListener('click', resetAnnouncementForm);
  $('admin-announcements').addEventListener('click', async event => {
    const editButton = event.target.closest('[data-edit-announcement]');
    if (editButton) {
      const item = adminAnnouncements.find(value => value.id === editButton.dataset.editAnnouncement);
      if (!item) return;
      announcementEditId = item.id;
      $('announcement-id').value = item.id;
      $('announcement-title').value = item.title;
      $('announcement-body').value = item.body;
      $('announcement-pinned').checked = item.pinned;
      $('announcement-form-title').textContent = '编辑公告';
      $('announcement-submit').textContent = '保存修改';
      $('announcement-cancel-edit').classList.remove('hidden');
      $('announcement-title').focus();
      return;
    }
    const deleteButton = event.target.closest('[data-delete-announcement]');
    if (!deleteButton || !confirm('删除这条公告？')) return;
    try {
      await api(`/api/admin/finance/announcements/${encodeURIComponent(deleteButton.dataset.deleteAnnouncement)}`, {method: 'DELETE'});
      await loadFinance();
    } catch (error) {
      setError('announcement-status', error.message);
    }
  });

  $('finance-batch-download').addEventListener('click', async () => {
    const ids = [...document.querySelectorAll('[data-finance-select]:checked')].map(input => input.dataset.financeSelect);
    if (!ids.length) {
      setError('announcement-status', '请先选择申请');
      return;
    }
    const response = await fetch('/api/admin/finance/download.zip', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({application_ids: ids}),
    });
    if (!response.ok) {
      setError('announcement-status', '批量下载失败');
      return;
    }
    const blob = await response.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `${new Date().toISOString().slice(0, 10).replaceAll('-', '')}报销申请.zip`;
    link.click();
    URL.revokeObjectURL(link.href);
  });

  $('admin-finance-rows').addEventListener('click', async event => {
    const button = event.target.closest('[data-review-status]');
    const row = event.target.closest('tr');
    if (!button || !row) return;
    const checkbox = row.querySelector('[data-finance-select]');
    const appId = checkbox?.dataset.financeSelect;
    if (!appId) return;
    const note = prompt(button.dataset.reviewStatus === 'approved' ? '审核备注（可选）' : '驳回原因（可选）') || '';
    try {
      await api(`/api/admin/finance/applications/${encodeURIComponent(appId)}/review`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({status: button.dataset.reviewStatus, note}),
      });
      await loadFinance();
    } catch (error) {
      setError('announcement-status', error.message);
    }
  });

  api('/api/test-features/status')
    .then(status => {
      isAdmin = ['admin', 'manager'].includes(status.role);
      $('api2api-admin').classList.toggle('hidden', !isAdmin);
      $('finance-admin').classList.toggle('hidden', !isAdmin);
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
