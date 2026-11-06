const codingPlanProviders = {
  zhipu: '智谱 GLM Coding Plan',
  kimi: 'Kimi Code',
  xiaomi: '小米 MiMo Token Plan',
};
const pendingCodingPlanQuotas = new Set();

function renderCodingPlanQuota(item, escapeHtml) {
  if (!item.provider) return '<span class="muted">未设置平台</span>';

  const quota = item.quota || {};
  const windows = (quota.windows || []).map(window => {
    const timestamp = window.period_end_at || window.reset_at;
    const timing = timestamp ? `<div class="muted">${escapeHtml(new Date(timestamp).toLocaleString())} ${window.period_end_at ? '周期结束' : '重置'}</div>` : '';
    return `<div>${escapeHtml(window.label)}剩余 <strong>${escapeHtml(window.remaining_percent)}%</strong>${timing}</div>`;
  }).join('');
  const updated = quota.updated_at ? `<div class="muted">更新于 ${escapeHtml(new Date(quota.updated_at).toLocaleString())}</div>` : '';
  const error = quota.error ? `<div class="muted" role="status">${escapeHtml(quota.error)}</div>` : '';
  let status = '';
  if (!item.quota_query_enabled) {
    status = '<div class="muted">当前环境未启用外部额度查询</div>';
  } else if (quota.pending || (!windows && !error)) {
    status = '<div class="muted">正在查询额度…</div>';
  }
  return `${windows}${updated}${error}${status}`;
}

function refreshVisibleCodingPlanQuotas(items, pathFor = path => path) {
  if (document.hidden) return;
  for (const item of items) {
    if (!item.quota_refresh_due || pendingCodingPlanQuotas.has(item.id)) continue;
    pendingCodingPlanQuotas.add(item.id);
    const path = `/api/coding-plans/${encodeURIComponent(item.id)}/quota`;
    fetch(pathFor(path), { method: 'POST', cache: 'no-store' })
      .catch(() => {})
      .finally(() => pendingCodingPlanQuotas.delete(item.id));
  }
}
