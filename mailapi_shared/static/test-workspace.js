(() => {
  const root = document.querySelector('.workspace-tree');
  if (!root) return;
  const byId = id => document.getElementById(id);
  const escape = value => {
    const span = document.createElement('span');
    span.textContent = String(value ?? '');
    return span.innerHTML.replaceAll('"', '&quot;');
  };
  async function request(url, options = {}) {
    const response = await fetch(url, {cache: 'no-store', ...options});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '读取失败，请重试');
    return data;
  }
  function action(kind, key, label) {
    return `<button type="button" class="btn-mini-action" data-workspace-action="${kind}" data-key="${escape(key)}">${label}</button>`;
  }
  function item(name, badges, detail, actions) {
    return `<div class="tree-item" data-workspace-search><div class="tree-item-info"><div class="tree-item-title"><span class="tree-account-name">${escape(name)}</span>${badges}</div><div class="tree-item-sub">${detail}</div></div><div class="tree-item-action">${actions}</div></div>`;
  }
  function gptItem(account) {
    const codex = account.codex || [];
    const plan = codex[0]?.chatgpt_plan_type || codex[0]?.subscription?.account_plan_type || codex[0]?.subscription?.plan_type;
    let badges = plan ? `<span class="workspace-plan">${escape(plan.toUpperCase())}</span>` : '';
    if (account.listed === false) badges += '<span class="workspace-plan" data-ui-text>已下架</span>';
    const automatic = codex.map(value => value.subscription).find(value => value?.available && Number.isFinite(value.expires_at));
    const expiry = subscriptionExpiry(automatic?.expires_at, account.subscription_expires_on || account.subscription_ends_on);
    const detail = expiry
      ? `<span class="${expiry.due ? 'workspace-expiry-due' : ''}" title="${escape(expiry.date)}">${expiry.remaining}</span>`
      : '订阅到期待补充';
    let buttons = action('login', account.login || account.email, '账号');
    if (account.has_gpt_password) buttons += action('password', account.gpt_password_account, '密码');
    if (account.has_totp_secret) buttons += action('totp', account.totp_secret_account, '2FA');
    buttons += '<a class="btn-mini-action" href="/#chatgpt-accounts">详情</a>';
    return item(account.login || account.email, badges, detail, buttons);
  }
  function planItem(plan) {
    return item(plan.name, '', escape(plan.note || plan.provider || 'Coding Plan'), action('plan', plan.id, '复制 Key'));
  }
  function relayItem(relay) {
    let buttons = relay.has_password ? action('relay', relay.id, '密码') : '';
    buttons += '<a class="btn-mini-action" href="/#relay-accounts">详情</a>';
    return item(relay.name, '', escape(relay.account || relay.note || '中转站'), buttons);
  }

  const search = byId('overview-search');
  function filterOverview() {
    const query = search.value.trim().toLocaleLowerCase();
    for (const row of document.querySelectorAll('[data-workspace-search], #dashboard-calls > li, #dashboard-finance > li')) {
      row.hidden = !row.textContent.toLocaleLowerCase().includes(query);
    }
    for (const section of root.querySelectorAll('.tree-section')) {
      const rows = [...section.querySelectorAll('[data-workspace-search]')];
      if (query && rows.some(row => !row.hidden)) setOpen(section, true);
    }
  }
  search.addEventListener('input', filterOverview);
  // Reapply the page filter as asynchronous resource and activity responses arrive.
  const observer = new MutationObserver(filterOverview);
  for (const id of ['workspace-gpt-items', 'workspace-plans-items', 'workspace-relays-items', 'dashboard-calls', 'dashboard-finance']) {
    observer.observe(byId(id), {childList: true});
  }
  function setOpen(section, open) {
    section.classList.toggle('open', open);
    section.querySelector('.tree-header').setAttribute('aria-expanded', String(open));
    section.querySelector('.tree-body-wrapper').inert = !open;
  }
  for (const button of root.querySelectorAll('.tree-header')) {
    button.addEventListener('click', () => {
      const section = button.closest('.tree-section');
      setOpen(section, !section.classList.contains('open'));
    });
  }

  root.addEventListener('click', async event => {
    const button = event.target.closest('[data-workspace-action]');
    if (!button || button.disabled) return;
    const kind = button.dataset.workspaceAction;
    const key = button.dataset.key;
    const label = button.textContent;
    button.disabled = true;
    const status = byId('workspace-action-status');
    status.textContent = '';
    try {
      let value;
      if (kind === 'login') value = key;
      else {
        const endpoints = {
          password: [`/api/accounts/${encodeURIComponent(key)}/gpt-password`, 'password'],
          totp: [`/api/accounts/${encodeURIComponent(key)}/totp-code`, 'code'],
          plan: [`/api/coding-plans/${encodeURIComponent(key)}/api-key`, 'api_key'],
          relay: [`/api/relay-accounts/${encodeURIComponent(key)}/password`, 'password'],
        };
        const [url, field] = endpoints[kind];
        value = (await request(url, {method: 'POST'}))[field];
      }
      if (!value) throw new Error('该信息暂不可用');
      await navigator.clipboard.writeText(value);
      button.textContent = '已复制';
      status.textContent = kind === 'totp' ? '已复制当前动态验证码，请及时使用。' : '已复制到剪贴板。';
    } catch (error) {
      status.textContent = error.message || '复制失败，请重试';
    } finally {
      setTimeout(() => { button.textContent = label; button.disabled = false; }, 1500);
    }
  });

  const groups = [
    ['gpt', '/api/accounts', gptItem],
    ['plans', '/api/coding-plans', planItem],
    ['relays', '/api/relay-accounts', relayItem],
  ];
  function loadResources() {
    return Promise.allSettled(groups.map(async ([name, url, render]) => {
      const data = await request(url);
      byId(`workspace-${name}-count`).textContent = data.items.length;
      byId(`workspace-${name}-items`).innerHTML = data.items.map(render).join('') || '<p class="workspace-empty" data-ui-text>暂无可用资源</p>';
      return data.items.length;
    })).then(results => {
      const failures = results.filter(result => result.status === 'rejected').length;
      byId('workspace-asset-count').textContent = failures ? '—' : results.reduce((total, result) => total + result.value, 0);
      byId('workspace-load-status').textContent = failures ? '部分资源加载失败，请刷新页面重试。' : '';
    });
  }
  loadResources();
  document.addEventListener('test:view-enter', loadResources);

  request('/api/auth/me').then(({user}) => {
    if (user.role !== 'admin') return;
    const link = byId('workspace-resource-link');
    link.href = '/admin';
    link.textContent = '＋ 快速导入账号或绑定邮箱';
  }).catch(() => {});
})();
