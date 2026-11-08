function renderAccountSubscription(item, escapeHtml, now = new Date()) {
  const start = item.subscription_started_on;
  const snapshots = (item.codex || []).map(account => account.subscription).filter(value => value?.source === 'id_token');
  const automatic = snapshots.find(value => value.available === true
    && Number.isFinite(value.expires_at) && Number.isFinite(value.valid_until)
    && now.getTime() / 1000 <= value.valid_until);
  const latest = snapshots.reduce((found, value) => (value.checked_at || 0) > (found?.checked_at || 0) ? value : found, null);
  const inspected = automatic || latest || snapshots[0];
  const checked = inspected?.checked_at;
  const checkedText = checked ? new Date(checked * 1000).toLocaleString('zh-CN', { hour12: false }) : '无记录';
  const manual = Boolean(start && item.subscription_ends_on);
  let note = '';
  if (!automatic && snapshots.length) {
    const reasons = {
      stale: '授权日期已超过 7 天', missing_dates: '授权日期缺失', invalid_dates: '授权日期无效',
      missing_check_time: '缺少授权核对时间', future_check_time: '授权核对时间异常', account_mismatch: '授权账户不匹配',
    };
    const reason = inspected?.available ? 'stale' : inspected?.unavailable_reason;
    note = `；${reasons[reason] || '授权日期不可用'}，${manual ? '已回退手填' : '请管理员补充'}`;
  }
  const metadata = `<div class="subscription-meta">授权上次核对：${escapeHtml(checkedText + note)}</div>`;
  let displayDate, days, source, title;
  if (automatic) {
    const end = new Date(automatic.expires_at * 1000);
    displayDate = [String(end.getFullYear()).slice(-2), String(end.getMonth() + 1).padStart(2, '0'), String(end.getDate()).padStart(2, '0')].join('.');
    days = Math.ceil((end.getTime() - now.getTime()) / 86400000);
    source = '授权文件 ID Token（到期日）';
    title = `${source}：${end.toLocaleString()}；上次核对：${checkedText}`;
  } else {
    if (!manual) return `<div class="subscription-time muted"><div>订阅时间：未设置</div>${metadata}</div>`;
    const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    days = Math.round((Date.parse(item.subscription_ends_on + 'T00:00:00Z') - today) / 86400000);
    displayDate = start.slice(2).replaceAll('-', '.');
    source = '手填开通日';
    title = '未取得 7 天内核对的授权日期；按手填开通日计算到期：' + item.subscription_ends_on;
  }
  const remaining = days <= 0 ? '已到期' : `${days} Day left`;
  return `<div class="subscription-time${days <= 5 ? ' subscription-due' : ''}" title="${escapeHtml(title)}"><div>订阅时间：${escapeHtml(displayDate)} (${remaining}) <span class="subscription-source">${escapeHtml(source)}</span></div>${metadata}</div>`;
}
