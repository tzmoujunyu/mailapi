const codingPlanProviders = {
  zhipu: '智谱 GLM Coding Plan',
  kimi: 'Kimi Code',
  xiaomi: '小米 MiMo Token Plan',
};
const pendingCodingPlanQuotas = new Set();

function renderQuotaMeter(label, remaining, details, escapeHtml) {
  if (!Number.isFinite(remaining)) return '';
  const percent = Math.max(0, Math.min(100, remaining));
  const title = escapeHtml(label);
  const meterLabel = title.replace(/"/g, '&quot;');
  const notes = details.map(text => `<div class="muted">${escapeHtml(text)}</div>`).join('');
  return `<div class="quota-cell${percent === 0 ? ' quota-empty' : ''}">
    <div class="quota-head"><span class="quota-title">${title}</span><span class="quota-value">${percent}%</span></div>
    <div class="quota-track" role="meter" aria-label="${meterLabel}剩余比例" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><div class="quota-fill" style="width:${percent}%"></div></div>
    ${notes}
  </div>`;
}

function renderCodingPlanQuota(item, escapeHtml, formatTime) {
  if (!item.provider) return '<span class="muted">未设置平台</span>';

  const quota = item.quota || {};
  const windows = (quota.windows || []).map(window => {
    const timestamp = window.period_end_at || window.reset_at;
    const details = timestamp ? [`${formatTime(timestamp)} ${window.period_end_at ? '周期结束' : '重置'}`] : [];
    return renderQuotaMeter(window.label, window.remaining_percent, details, escapeHtml);
  }).join('');
  const updated = quota.updated_at ? `<div class="muted">更新于 ${escapeHtml(formatTime(quota.updated_at))}</div>` : '';
  const error = quota.error ? `<div class="muted" role="status">${escapeHtml(quota.error)}</div>` : '';
  let status = '';
  if (!item.quota_query_enabled) {
    status = '<div class="muted">当前环境未启用外部额度查询</div>';
  } else if (quota.pending || (!windows && !error)) {
    status = '<div class="muted">正在查询额度…</div>';
  }
  return `<div class="quota-stack">${windows}${updated}${error}${status}</div>`;
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
