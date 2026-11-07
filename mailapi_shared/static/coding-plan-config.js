// Official mainland Coding Plan configuration; sources and review date are in docs/development.md.
const codingPlanConfigs = {
  xiaomi: {
    label: '小米 MiMo 模型与接入文档',
    docs: 'https://mimo.mi.com/docs/zh-CN/tokenplan/Token%20Plan/subscription',
    urls: [['OpenAI', 'https://token-plan-cn.xiaomimimo.com/v1'], ['Anthropic', 'https://token-plan-cn.xiaomimimo.com/anthropic']],
    note: '中国集群；以套餐控制台所属集群为准',
  },
  zhipu: {
    label: '智谱 GLM 模型与调用说明',
    docs: 'https://docs.bigmodel.cn/cn/coding-plan/latest-model',
    urls: [['OpenAI · Chat Completions', 'https://open.bigmodel.cn/api/coding/paas/v4'], ['OpenAI · Responses', 'https://open.bigmodel.cn/api/v1'], ['Anthropic', 'https://open.bigmodel.cn/api/anthropic']],
  },
  kimi: {
    label: 'Kimi Code 模型与调用说明',
    docs: 'https://www.kimi.com/code/docs/kimi-code/models.html',
    urls: [['OpenAI', 'https://api.kimi.com/coding/v1'], ['Anthropic', 'https://api.kimi.com/coding/']],
  },
};

function renderCodingPlanDocs(item, escapeHtml) {
  const config = codingPlanConfigs[item.provider];
  return config ? `<div class="plan-docs"><a class="resource-link" href="${escapeHtml(config.docs)}" target="_blank" rel="noopener noreferrer">${escapeHtml(config.label)}</a></div>` : '';
}

function renderCodingPlanBaseUrls(item, escapeHtml, buttonClass) {
  const config = codingPlanConfigs[item.provider];
  if (!config) return '<span class="muted">未设置平台</span>';
  return config.urls.map(([protocol, url]) => `<div class="plan-endpoint"><div class="muted">${escapeHtml(protocol)}</div><div class="plan-copy-line"><code>${escapeHtml(url)}</code><button class="${buttonClass}" type="button" data-base-url="${escapeHtml(url)}">复制</button></div></div>`).join('')
    + (config.note ? `<div class="muted">${escapeHtml(config.note)}</div>` : '');
}

async function copyCodingPlanValue(button, readValue) {
  const label = button.textContent;
  button.disabled = true;
  try {
    const value = await readValue();
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(value);
    else {
      const input = document.createElement('textarea');
      input.value = value; input.style.position = 'fixed'; input.style.opacity = '0';
      document.body.appendChild(input); input.focus(); input.select();
      const copied = document.execCommand('copy'); input.remove();
      if (!copied) throw new Error('浏览器拒绝访问剪贴板');
    }
    button.textContent = '已复制';
  } catch (error) { button.textContent = '复制失败'; button.title = error.message; }
  finally { setTimeout(() => { button.textContent = label; button.disabled = false; }, 1500); }
}
