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
    sidebar.querySelector('.test-ui-logo').insertAdjacentHTML('afterend', '<span class="test-ui-brand-name">MailAPI</span>');
    const footer = sidebar.querySelector('.test-ui-sidebar-footer');
    footer.querySelector('a')?.remove();
    const collapse = document.createElement('button');
    collapse.type = 'button';
    collapse.className = 'test-ui-dock-collapse';
    collapse.innerHTML = icon('sidebar');
    const expand = document.createElement('button');
    expand.type = 'button';
    expand.className = 'test-ui-dock-expand';
    expand.innerHTML = icon('sidebar-expand');
    sidebar.append(collapse);
    const heading = document.querySelector('.topbar, body > header, .home-directory-inner');
    heading?.prepend(expand);
    const toggles = [collapse, expand];
    for (const toggle of toggles) toggle.setAttribute('aria-controls', nav.id);
    function setExpanded(expanded) {
      document.body.classList.toggle('dock-expanded', expanded);
      for (const toggle of toggles) {
        toggle.setAttribute('aria-expanded', String(expanded));
        toggle.title = toggle === collapse ? '收起边栏' : '展开边栏';
        toggle.setAttribute('aria-label', toggle.title);
      }
    }
    let expanded = false;
    try { expanded = sessionStorage.getItem('mailapi.test.dock') === 'expanded'; } catch {}
    setExpanded(expanded);
    for (const toggle of toggles) {
      toggle.addEventListener('click', () => {
        const next = !document.body.classList.contains('dock-expanded');
        setExpanded(next);
        try { sessionStorage.setItem('mailapi.test.dock', next ? 'expanded' : 'compact'); } catch {}
        (next ? collapse : expand).focus();
      });
    }

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
  }

  const container = document.querySelector('.test-ui-home .container');
  if (!container) return;
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
    const list = document.querySelector(listSelector);
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
})();
