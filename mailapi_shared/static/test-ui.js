(() => {
  const path = location.pathname;
  const pageName = path === '/' ? 'home' : path === '/admin' ? 'admin' : path === '/admin/access' ? 'access' : path === '/login' ? 'login' : '';
  if (!pageName || document.querySelector('.workspace')) return;

  document.body.classList.add('test-ui-page', `test-ui-${pageName}`);

  function buildSidebar() {
    const aside = document.createElement('aside');
    aside.className = 'test-ui-sidebar';
    aside.setAttribute('aria-label', '测试版导航');
    const links = [
      ['⌂', '主页', '/'],
      ['▦', '账户', '/admin/access'],
      ['◈', '资源', '/admin'],
      ['¥', '财务', '/test-features'],
    ];
    aside.innerHTML = `<div class="test-ui-logo" aria-label="MailAPI 测试版">M</div><nav class="test-ui-nav">${links.map(([icon, label, href]) => `<a href="${href}" aria-label="${label}" title="${label}" ${href === path ? 'aria-current="page"' : ''}><span aria-hidden="true">${icon}</span><span class="test-ui-nav-label">${label}</span></a>`).join('')}</nav><div class="test-ui-sidebar-footer"><a href="/login" aria-label="退出并重新登录" title="登录">↻</a></div>`;
    document.body.prepend(aside);
  }

  function buildLoginBrand() {
    const form = document.querySelector('.login-main form');
    if (!form || form.querySelector('.test-ui-login-brand')) return;
    const brand = document.createElement('div');
    brand.className = 'test-ui-login-brand';
    brand.textContent = 'M';
    form.prepend(brand);
  }

  function buildHomeSummary() {
    const container = document.querySelector('.home-page .container');
    if (!container || document.querySelector('.test-ui-home-summary')) return;
    const summary = document.createElement('div');
    summary.className = 'test-ui-home-summary';
    summary.innerHTML = [
      ['GPT 账号', 'home-gpt-count', '可用账户'],
      ['Coding Plan', 'home-plan-count', '订阅资源'],
      ['中转站', 'home-relay-count', '可用入口'],
      ['使用提示', 'home-tip-count', '帮助内容'],
    ].map(([label, id, foot]) => `<article class="test-ui-summary-card"><span class="test-ui-summary-label">${label}</span><strong id="${id}" class="test-ui-summary-value">—</strong><small class="test-ui-summary-foot">${foot}</small></article>`).join('');
    container.prepend(summary);

    const countRows = selector => {
      const node = document.querySelector(selector);
      if (!node) return '—';
      const children = [...node.children];
      return children.filter(row => {
        if (row.classList.contains('detail-row')) return false;
        if (selector === '#rows') return Boolean(row.querySelector('.account-email'));
        return !row.querySelector('.muted');
      }).length;
    };
    const update = () => {
      const gpt = document.querySelector('#rows');
      const gptRows = gpt ? [...gpt.children].filter(row => !row.classList.contains('detail-row') && row.querySelector('.account-email')).length : 0;
      const value = (selector, empty = 0) => {
        const count = countRows(selector);
        return count === '—' ? '—' : count || empty;
      };
      document.getElementById('home-gpt-count').textContent = gptRows || '0';
      document.getElementById('home-plan-count').textContent = value('#coding-plan-rows');
      document.getElementById('home-relay-count').textContent = value('#relay-account-rows');
      document.getElementById('home-tip-count').textContent = value('#codex-tip-list');
    };
    const observer = new MutationObserver(update);
    ['#rows', '#coding-plan-rows', '#relay-account-rows', '#codex-tip-list'].forEach(selector => {
      const node = document.querySelector(selector);
      if (node) observer.observe(node, {childList: true, subtree: true});
    });
    update();
  }

  function markSessionReady() {
    document.querySelector('.site-session')?.classList.add('test-ui-session-ready');
  }

  if (!document.querySelector('.test-ui-sidebar')) buildSidebar();
  if (pageName === 'login') buildLoginBrand();
  if (pageName === 'home') buildHomeSummary();
  markSessionReady();

  const observer = new MutationObserver(() => {
    markSessionReady();
    if (pageName === 'home') buildHomeSummary();
  });
  observer.observe(document.body, {childList: true, subtree: false});
})();
