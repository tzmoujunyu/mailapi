(() => {
  const dialog = document.getElementById('relay-mail-dialog');
  const content = document.getElementById('relay-mail-content');
  const title = document.getElementById('relay-mail-title');
  let opened = null, timer = null;

  async function refresh() {
    const view = opened;
    if (!view || !dialog.open || document.hidden || view.loading) return;
    view.loading = true;
    try {
      const response = await fetch(previewUrl(`/api/relay-accounts/${encodeURIComponent(view.id)}/mailbox`), {
        cache: 'no-store', signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) throw new Error(response.status === 404
        ? '接码配置不可用或分组权限已变更。' : '邮箱验证码加载失败，请稍后重试。');
      const { item, history } = await response.json();
      if (!dialog.open || opened !== view) return;
      const markup = `<strong>${escapeHtml(item.email)}</strong>
        <div class="muted">${escapeHtml(item.provider)} · ${escapeHtml(item.enabled === false ? '已停用' : item.status)}</div>
        <div class="relay-mail-latest">
          <strong>最新接收</strong>
          <div>${mailResultHtml(item.result_type, item.result_value || item.last_code)}</div>
          <div>${mailSourceTag(item.rule_name, item.subject)}${escapeHtml(item.subject || '-')}</div>
          <div class="muted">${escapeHtml(formatDateTime(item.updated_at))}</div>
        </div>
        ${history.length ? renderMailHistory(history) : '<p class="muted">暂无历史收件记录。</p>'}`;
      if (view.markup !== markup) {
        view.markup = markup;
        content.innerHTML = markup;
      }
    } catch (failure) {
      if (dialog.open && opened === view) {
        view.markup = '';
        content.textContent = failure.name === 'TimeoutError' ? '邮箱验证码加载超时，请稍后重试。' : failure.message;
      }
    } finally {
      view.loading = false;
    }
  }

  relayAccountRows.addEventListener('click', event => {
    const button = event.target.closest('[data-relay-mail]');
    if (!button || dialog.open) return;
    const relay = relayAccounts.find(item => item.id === button.dataset.relayMail);
    title.textContent = relay ? `邮箱验证码 · ${relay.name}` : '邮箱验证码';
    opened = { id: button.dataset.relayMail, loading: false, markup: '' };
    content.textContent = '正在加载邮箱验证码…';
    dialog.showModal();
    timer = setInterval(refresh, HISTORY_REFRESH_MS);
    return refresh();
  });
  content.addEventListener('click', event => {
    const copy = event.target.closest('[data-copy-type="mail"]');
    if (copy) return copySecret(copy);
  });
  dialog.addEventListener('close', () => {
    clearInterval(timer);
    opened = null;
    content.textContent = '';
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
