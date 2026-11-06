const codingPlanProviders = { zhipu: '智谱 GLM Coding Plan', kimi: 'Kimi Code' };

function renderCodingPlanQuota(item, escapeHtml, escapeAttr, buttonClass) {
  if (!item.provider) return '<span class="muted">未设置平台</span>';

  const quota = item.quota || {};
  const windows = (quota.windows || []).map(window => {
    const reset = window.reset_at ? `<div class="muted">${escapeHtml(new Date(window.reset_at).toLocaleString())} 重置</div>` : '';
    return `<div>${escapeHtml(window.label)}剩余 <strong>${escapeHtml(window.remaining_percent)}%</strong>${reset}</div>`;
  }).join('');
  const updated = quota.updated_at ? `<div class="muted">更新于 ${escapeHtml(new Date(quota.updated_at).toLocaleString())}</div>` : '';
  const error = quota.error ? `<div class="muted" role="status">${escapeHtml(quota.error)}</div>` : '';
  const label = quota.pending ? '查询中…' : quota.updated_at ? '刷新额度' : '查询额度';
  const button = item.quota_query_enabled
    ? `<button class="${buttonClass}" type="button" data-coding-plan-quota="${escapeAttr(item.id)}"${quota.pending ? ' disabled' : ''}>${label}</button>`
    : '<div class="muted">当前环境未启用外部额度查询</div>';
  return `${windows}${updated}${error}${button}`;
}
