function subscriptionExpiry(expiresAt, expiresOn, now = new Date()) {
  const automatic = Number.isFinite(expiresAt);
  const end = new Date(automatic ? expiresAt * 1000 : `${expiresOn}T00:00:00`);
  if (!Number.isFinite(end.getTime())) return null;
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const days = automatic ? Math.ceil((end.getTime() - now.getTime()) / 86400000)
    : Math.round((Date.parse(expiresOn + 'T00:00:00Z') - today) / 86400000);
  const date = automatic ? [String(end.getFullYear()).slice(-2), String(end.getMonth() + 1).padStart(2, '0'), String(end.getDate()).padStart(2, '0')].join('.')
    : expiresOn.slice(2).replaceAll('-', '.');
  return { end, date, remaining: days <= 0 ? '已到期' : `${days} Day left`, due: days <= 5 };
}

function renderAccountSubscription(item, escapeHtml, now = new Date()) {
  const start = item.subscription_started_on;
  const manualEnd = item.subscription_expires_on || item.subscription_ends_on;
  const snapshots = (item.codex || []).map(account => account.subscription).filter(value => value?.source === 'id_token');
  const automatic = snapshots.find(value => value.available === true
    && Number.isFinite(value.expires_at));
  const latest = snapshots.reduce((found, value) => (value.checked_at || 0) > (found?.checked_at || 0) ? value : found, null);
  const inspected = automatic || latest || snapshots[0];
  const checked = inspected?.checked_at;
  const checkedText = checked ? new Date(checked * 1000).toLocaleString('zh-CN', { hour12: false }) : '无记录';
  const manual = Boolean(manualEnd);
  let note = '';
  if (!automatic && snapshots.length) {
    const reasons = {
      missing_dates: '授权日期缺失', invalid_dates: '授权日期无效', account_mismatch: '授权账户不匹配',
    };
    const reason = inspected?.unavailable_reason;
    note = `；${reasons[reason] || '授权日期不可用'}，${manual ? '已回退手填' : '请管理员补充'}`;
  }
  const metadata = `<div class="subscription-meta">授权上次核对：${escapeHtml(checkedText + note)}</div>`;
  const expiry = subscriptionExpiry(automatic?.expires_at, manualEnd, now);
  if (!expiry) return `<div class="subscription-time muted"><div data-ui-text>订阅到期：未设置</div>${metadata}</div>`;
  let displayDate = expiry.date;
  let source, title, label;
  if (automatic) {
    source = '授权文件 ID Token（到期日）';
    label = '订阅到期';
    title = `${source}：${expiry.end.toLocaleString()}；上次核对：${checkedText}`;
  } else {
    const direct = Boolean(item.subscription_expires_on);
    if (!direct) displayDate = start.slice(2).replaceAll('-', '.');
    source = direct ? '手填到期日' : '手填开通日';
    label = direct ? '订阅到期' : '订阅开通';
    title = `${source}计算到期：${manualEnd}`;
  }
  return `<div class="subscription-time${expiry.due ? ' subscription-due' : ''}" title="${escapeHtml(title)}"><div>${label}：${escapeHtml(displayDate)} (${expiry.remaining}) <span class="subscription-source">${escapeHtml(source)}</span></div>${metadata}</div>`;
}
