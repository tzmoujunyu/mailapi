(() => {
  const rows = document.getElementById('mailbox-resource-rows');
  const error = document.getElementById('mailbox-error');
  let mailboxes = [], opened = '', history = [], historyError = '';
  let refreshing = false, loading = false, request = 0;

  function render() {
    rows.innerHTML = mailboxes.length ? mailboxes.map(mail => {
      let detail = '';
      if (opened === mail.name) {
        const content = loading ? '历史记录加载中…' : historyError || (history.length ? renderMailHistory(history) : '暂无历史收件记录。');
        detail = `<tr class="detail-row"><td colspan="5"><div class="detail-panel">${loading || historyError || !history.length ? `<span class="muted">${escapeHtml(content)}</span>` : content}</div></td></tr>`;
      }
      return `<tr><td><div class="account-main"><span class="account-email">${escapeHtml(mail.email)}</span></div><div class="muted">${escapeHtml(mail.provider)}${mail.listed === false ? ' · 未上架（仅管理员可见）' : ''}</div>${mail.note ? `<div class="muted mailbox-note">${escapeHtml(mail.note)}</div>` : ''}</td>
        <td>${mailResultHtml(mail.result_type, mail.result_value || mail.last_code)}<div class="muted">${escapeHtml(formatDateTime(mail.updated_at))}</div></td>
        <td>${mailSourceTag(mail.rule_name, mail.subject)}${escapeHtml(mail.subject || '-')}</td>
        <td><span class="${mail.enabled === false ? 'status-warn' : 'muted'}">${escapeHtml(mail.enabled === false ? '已停用' : mail.status || '未知')}</span></td>
        <td><button class="small-btn" data-mailbox-history="${escapeAttr(mail.name)}" aria-expanded="${opened === mail.name}">${opened === mail.name ? '收起历史' : '查看历史'}</button></td></tr>${detail}`;
    }).join('') : '<tr><td colspan="5" class="muted">暂无可用邮箱，请联系管理员上架并分配到分组。</td></tr>';
  }

  function closeHistory() { opened = ''; history = []; historyError = ''; loading = false; request++; }

  async function loadHistory() {
    const name = opened, version = ++request;
    loading = true; historyError = ''; render();
    try {
      const response = await fetch(previewUrl(`/api/history/${encodeURIComponent(name)}?scope=mailbox`), { cache: 'no-store' });
      if (!response.ok) throw new Error('历史记录加载失败或访问权限已撤销。');
      const data = await response.json();
      if (version === request && name === opened) history = data.items || [];
    } catch (failure) {
      if (version === request && name === opened) { history = []; historyError = failure.message; }
    } finally {
      if (version === request && name === opened) { loading = false; render(); }
    }
  }

  async function refresh() {
    if (refreshing || rows.querySelector('[data-copy-type]:disabled')) return;
    refreshing = true;
    try {
      const response = await fetch(previewUrl('/api/mailboxes'), { cache: 'no-store' });
      if (!response.ok) throw new Error('邮箱接码加载失败，请重新登录或稍后重试。');
      mailboxes = (await response.json()).items || [];
      if (opened && !mailboxes.some(mail => mail.name === opened)) closeHistory();
      error.textContent = ''; render();
      if (opened && !loading) loadHistory();
    } catch (failure) { error.textContent = failure.message; }
    finally { refreshing = false; }
  }

  rows.addEventListener('click', event => {
    const button = event.target.closest('[data-mailbox-history]');
    if (button) {
      if (opened === button.dataset.mailboxHistory) { closeHistory(); render(); }
      else { closeHistory(); opened = button.dataset.mailboxHistory; loadHistory(); }
      return;
    }
    const copy = event.target.closest('[data-copy-type]');
    if (copy) copySecret(copy);
  });
  refresh();
  setInterval(refresh, HISTORY_REFRESH_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();

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
