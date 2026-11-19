(() => {
  const sidebar = document.querySelector('.test-ui-sidebar');
  if (sidebar) {
    const icons = {
      '/test-features#view-overview': 'home', '/': 'resources', '/admin/access': 'accounts',
      '/admin': 'console', '/test-features#view-api2api': 'api', '/test-features#view-finance': 'finance',
    };
    const icon = name => `<svg aria-hidden="true" viewBox="0 0 24 24"><use href="/static/test-navigation.svg#${name}"></use></svg>`;
    const nav = sidebar.querySelector('.test-ui-nav');
    nav.id = 'workspace-navigation';
    for (const link of nav.querySelectorAll('a')) {
      const name = icons[link.getAttribute('href')];
      if (name) link.firstElementChild.innerHTML = icon(name);
    }
    sidebar.querySelector('.test-ui-logo')?.remove();
    const header = document.createElement('header');
    header.className = 'test-ui-header';
    header.innerHTML = `<button class="test-ui-dock-toggle" type="button" aria-controls="${nav.id}">${icon('sidebar-expand')}</button><a class="test-ui-brand-name" href="/test-features#view-overview">MILVLG</a><div class="test-ui-header-search"></div><div class="test-ui-header-actions"></div>`;
    document.body.prepend(header);
    const search = document.querySelector('.workspace-search, .management-search');
    if (search) header.querySelector('.test-ui-header-search').append(search);
    const account = sidebar.querySelector('.test-ui-account');
    if (account) header.querySelector('.test-ui-header-actions').append(account);
    sidebar.querySelector('.test-ui-sidebar-footer a')?.remove();
    for (const link of nav.querySelectorAll('a')) {
      link.removeAttribute('title');
      const tooltip = document.createElement('span');
      tooltip.className = 'test-ui-nav-tooltip';
      tooltip.setAttribute('aria-hidden', 'true');
      tooltip.textContent = link.getAttribute('aria-label') || link.textContent;
      link.append(tooltip);
    }
    const toggle = header.querySelector('button');
    function setExpanded(expanded) {
      document.body.classList.toggle('dock-expanded', expanded);
      toggle.setAttribute('aria-expanded', String(expanded));
      toggle.title = expanded ? '收起边栏' : '展开边栏';
      toggle.setAttribute('aria-label', toggle.title);
    }
    let expanded = false;
    try { expanded = sessionStorage.getItem('mailapi.test.dock') === 'expanded'; } catch {}
    setExpanded(expanded);
    setTimeout(() => document.body.classList.add('dock-ready'), 0);
    toggle.addEventListener('click', () => {
      const next = !document.body.classList.contains('dock-expanded');
      setExpanded(next);
      try { sessionStorage.setItem('mailapi.test.dock', next ? 'expanded' : 'compact'); } catch {}
    });

    function syncCurrentNav() {
      let target = location.pathname;
      if (target === '/test-features') {
        target += ['#view-api2api', '#view-finance'].includes(location.hash) ? location.hash : '#view-overview';
      }
      for (const link of nav.querySelectorAll('a')) {
        if (link.getAttribute('href') === target) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
      }
    }
    syncCurrentNav();
    window.addEventListener('hashchange', syncCurrentNav);
    window.addEventListener('test:navigation', syncCurrentNav);
  }

  function enhanceResources(root) {
    const container = root.querySelector('.container');
    if (!container || !root.querySelector('#chatgpt-accounts') || container.querySelector('.test-ui-home-summary')) return;
    const sections = [
      ['chatgpt-accounts', 'GPT 账号', '#rows', '.account-email'],
      ['codex-tips', 'Codex 使用提示', '#codex-tip-list', '.codex-tip-item'],
      ['coding-plans', 'Coding Plan', '#coding-plan-rows', '.resource-name'],
      ['relay-accounts', '中转站', '#relay-account-rows', '.resource-name'],
    ];
    const summary = document.createElement('nav');
    summary.className = 'test-ui-home-summary';
    summary.setAttribute('aria-label', '资源数量与分类');
    for (const [id, label, listSelector, itemSelector] of sections) {
      const list = root.querySelector(listSelector);
      const link = document.createElement('a');
      link.href = '#' + id;
      link.className = 'test-ui-summary-card';
      link.innerHTML = `<span class="test-ui-summary-label">${label}</span><strong class="test-ui-summary-value">—</strong>`;
      const update = () => { link.lastElementChild.textContent = list.querySelectorAll(itemSelector).length; };
      new MutationObserver(update).observe(list, { childList: true });
      update();
      summary.append(link);
    }
    container.prepend(summary);
  }
  window.TestUI = {enhanceResources};
  if (!document.querySelector('[data-test-view]') && document.body.classList.contains('test-ui-home')) enhanceResources(document);
})();
