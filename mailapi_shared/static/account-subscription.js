function renderAccountSubscription(item, escapeHtml, now = new Date()) {
  const start = item.subscription_started_on;
  const automatic = (item.codex || []).filter(account => !account.subscription_error && !account.reauth_required)
    .flatMap(account => ['expires_at', 'renews_at'].map(field => ({
      timestamp: account.subscription?.[field], field,
    })))
    .find(value => Number.isFinite(value.timestamp) && value.timestamp > 0
      && Number.isFinite(new Date(value.timestamp * 1000).getTime()));
  let displayDate, days, source, title;
  if (automatic) {
    const end = new Date(automatic.timestamp * 1000);
    displayDate = [String(end.getFullYear()).slice(-2), String(end.getMonth() + 1).padStart(2, '0'), String(end.getDate()).padStart(2, '0')].join('.');
    days = Math.ceil((end.getTime() - now.getTime()) / 86400000);
    source = automatic.field === 'expires_at' ? '自动到期日' : '自动续费日';
    title = `${source}：${end.toLocaleString()}`;
  } else {
    if (!start || !item.subscription_ends_on) return '<div class="subscription-time muted" title="未读取到自动订阅日期，可由管理员手动补充">订阅时间：未设置</div>';
    const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    days = Math.round((Date.parse(item.subscription_ends_on + 'T00:00:00Z') - today) / 86400000);
    displayDate = start.slice(2).replaceAll('-', '.');
    source = '手填开通日';
    title = '未读取到自动日期；按手填开通日计算到期：' + item.subscription_ends_on;
  }
  const remaining = days <= 0 ? '已到期' : `${days} Day left`;
  return `<div class="subscription-time${days <= 5 ? ' subscription-due' : ''}" title="${escapeHtml(title)}">订阅时间：${escapeHtml(displayDate)} (${remaining}) <span class="subscription-source">${escapeHtml(source)}</span></div>`;
}
