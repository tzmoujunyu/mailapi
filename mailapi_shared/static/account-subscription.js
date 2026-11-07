function renderAccountSubscription(item, escapeHtml, now = new Date()) {
  const start = item.subscription_started_on;
  if (!start || !item.subscription_ends_on) return '<div class="subscription-time muted">订阅时间：未设置</div>';
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const days = Math.round((Date.parse(item.subscription_ends_on + 'T00:00:00Z') - today) / 86400000);
  const remaining = days <= 0 ? '已到期' : `${days} Day left`;
  return `<div class="subscription-time${days <= 5 ? ' subscription-due' : ''}" title="${escapeHtml('本次订阅到期：' + item.subscription_ends_on)}">订阅时间：${escapeHtml(start.slice(2).replaceAll('-', '.'))} (${remaining})</div>`;
}
