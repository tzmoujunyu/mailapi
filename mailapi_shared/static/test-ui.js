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
    const mobile = window.matchMedia?.('(max-width: 767px)');
    const backdrop = document.createElement('button');
    backdrop.className = 'test-ui-dock-backdrop';
    backdrop.type = 'button';
    backdrop.tabIndex = -1;
    backdrop.setAttribute('aria-hidden', 'true');
    backdrop.hidden = true;
    sidebar.after(backdrop);
    const inertNodes = new Set();
    let desktopExpanded = false;
    let mobileExpanded = false;
    function setExpanded(expanded) {
      document.body.classList.toggle('dock-expanded', expanded);
      const modal = Boolean(mobile?.matches && expanded);
      document.body.classList.toggle('dock-modal', modal);
      backdrop.hidden = !modal;
      if (modal) {
        document.querySelectorAll('[data-test-view], .test-ui-header-search, .test-ui-header-actions, .test-ui-brand-name').forEach(node => {
          if (!node.inert) { node.inert = true; inertNodes.add(node); }
        });
      } else {
        inertNodes.forEach(node => { node.inert = false; });
        inertNodes.clear();
      }
      toggle.setAttribute('aria-expanded', String(expanded));
      toggle.title = expanded ? '收起边栏' : '展开边栏';
      toggle.setAttribute('aria-label', toggle.title);
    }
    function closeMobile() {
      if (!mobileExpanded) return;
      mobileExpanded = false;
      setExpanded(false);
      toggle.focus();
    }
    try { desktopExpanded = sessionStorage.getItem('mailapi.test.dock') === 'expanded'; } catch {}
    setExpanded(mobile?.matches ? false : desktopExpanded);
    setTimeout(() => document.body.classList.add('dock-ready'), 0);
    toggle.addEventListener('click', () => {
      const next = !document.body.classList.contains('dock-expanded');
      if (mobile?.matches) mobileExpanded = next;
      else {
        desktopExpanded = next;
        try { sessionStorage.setItem('mailapi.test.dock', next ? 'expanded' : 'compact'); } catch {}
      }
      setExpanded(next);
    });
    backdrop.addEventListener('click', closeMobile);
    nav.addEventListener('click', event => { if (event.target.closest('a')) closeMobile(); });
    mobile?.addEventListener('change', () => {
      mobileExpanded = false;
      setExpanded(mobile.matches ? false : desktopExpanded);
    });
    document.addEventListener('keydown', event => {
      if (!mobileExpanded) return;
      if (event.key === 'Escape') { event.preventDefault(); closeMobile(); }
      if (event.key === 'Tab') {
        const links = [...nav.querySelectorAll('a[href]')];
        const first = toggle, last = links.at(-1) || toggle;
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });

    function syncCurrentNav() {
      let target = location.pathname;
      if (target === '/preview') target = '/';
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
    if (!container || !root.querySelector('#chatgpt-accounts') || container.querySelector('.resource-search-tools')) return;
    const sections = [
      ['chatgpt-accounts', '#rows', '.account-email'],
      ['codex-tips', '#codex-tip-list', '.codex-tip-item'],
      ['coding-plans', '#coding-plan-rows', '.resource-name'],
      ['relay-accounts', '#relay-account-rows', '.resource-name'],
    ];
    for (const [id, listSelector, itemSelector] of sections) {
      const list = root.querySelector(listSelector);
      const link = root.querySelector(`.home-directory a[href="#${id}"]`);
      if (!list || !link) continue;
      const count = document.createElement('span');
      count.className = 'resource-count';
      link.append(count);
      const update = () => { count.textContent = list.querySelectorAll(itemSelector).length; };
      new MutationObserver(update).observe(list, { childList: true });
      update();
    }
    const section = root.querySelector('#chatgpt-accounts');
    const tools = document.createElement('div');
    tools.className = 'resource-search-tools';
    tools.innerHTML = `<label class="resource-search-field"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg><input type="search" aria-label="搜索 GPT 账号" placeholder="搜索账号、套餐或状态…" autocomplete="off"></label><button type="button" class="small-btn" hidden>清除</button><span class="resource-search-count" role="status" aria-live="polite"></span>`;
    section.querySelector('.page-head').append(tools);
    const input = tools.querySelector('input'), clear = tools.querySelector('button'), status = tools.querySelector('[role="status"]');
    const rows = section.querySelector('#rows');
    const empty = document.createElement('p');
    empty.className = 'resource-search-empty muted';
    empty.textContent = '没有匹配的账号，请调整关键词。'; empty.hidden = true;
    section.querySelector('.table-wrap').after(empty);
    const normalize = value => value.normalize('NFKC').toLocaleLowerCase();
    function filter() {
      const terms = normalize(input.value).trim().split(/\s+/).filter(Boolean);
      let total = 0, shown = 0, parentVisible = false;
      for (const row of rows.children) {
        if (row.querySelector('.account-email')) {
          total++;
          parentVisible = terms.every(term => normalize(row.textContent).includes(term));
          if (parentVisible) shown++;
          row.hidden = !parentVisible;
        } else row.hidden = row.classList.contains('detail-row') ? !parentVisible : terms.length > 0;
      }
      status.textContent = `${shown} / ${total} ${window.SiteLanguage?.t('账号') || '账号'}`;
      clear.hidden = !input.value; empty.hidden = !terms.length || shown > 0;
    }
    let timer;
    const schedule = () => { clearTimeout(timer); timer = setTimeout(filter, 150); };
    input.addEventListener('input', event => { if (!event.isComposing) schedule(); });
    input.addEventListener('compositionend', schedule);
    const reset = () => { clearTimeout(timer); input.value = ''; filter(); input.focus(); };
    clear.addEventListener('click', reset);
    input.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); reset(); } });
    new MutationObserver(filter).observe(rows, { childList: true });
    document.addEventListener('site:language', filter);
    filter();
  }
  window.TestUI = {enhanceResources};
  if (!document.querySelector('[data-test-view]') && document.body.classList.contains('test-ui-home')) enhanceResources(document);
  document.addEventListener('keydown', event => {
    const details = event.target.closest?.('.console-disclosure[open], .subscription-details[open]');
    if (event.key === 'Escape' && details) {
      event.preventDefault(); event.stopPropagation(); details.open = false; details.querySelector('summary').focus();
    }
  });
})();

// Delegate hints so controls added by the persistent router need no remount.
(() => {
  if (!document.body.classList.contains('test-ui-page')) return;
  document.body.classList.add('test-has-tooltips');
  const selector = 'button,a[href],summary,[role="button"],input[type="button"],input[type="submit"]';
  const excluded = '.help-hint-trigger,.site-theme-button,[data-fin-chart],[aria-haspopup="menu"][aria-expanded="true"]';
  const tip = document.createElement('div');
  tip.id = 'test-control-tooltip';
  tip.className = 'test-control-tooltip';
  tip.setAttribute('role', 'tooltip');
  tip.setAttribute('popover', 'manual');
  tip.setAttribute('data-i18n-skip', '');
  tip.hidden = true;
  let anchor, pending, showTimer, hideTimer, savedTitle, savedLabel, pointerFocus = false;
  function visibleText(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent;
    if (node.nodeType !== Node.ELEMENT_NODE || node.matches('svg,[hidden],[aria-hidden="true"],.sr-only,.visually-hidden')) return '';
    const style = getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return '';
    return [...node.childNodes].map(visibleText).join('');
  }
  function label(control) {
    if (control.matches('.test-ui-avatar-button')) return control.getAttribute('aria-label');
    return control.getAttribute('title') || (control === anchor ? savedTitle : '') || control.getAttribute('aria-label') ||
      (control.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
  }
  function candidate(target) {
    const control = target instanceof Element ? target.closest(selector) : null;
    if (!control || control.namespaceURI !== 'http://www.w3.org/1999/xhtml' || control.matches(excluded) || control.closest('[hidden],[inert],[aria-hidden="true"]')) return null;
    if (!label(control) || (!control.hasAttribute('title') && /[\p{L}\p{N}]/u.test(visibleText(control)))) return null;
    return control;
  }
  function hide() {
    clearTimeout(showTimer); clearTimeout(hideTimer); pending = null;
    if (!anchor) return;
    tip.hidePopover?.(); tip.hidden = true;
    const ids = (anchor.getAttribute('aria-describedby') || '').split(/\s+/).filter(id => id && id !== tip.id);
    if (ids.length) anchor.setAttribute('aria-describedby', ids.join(' '));
    else anchor.removeAttribute('aria-describedby');
    if (savedTitle && !anchor.hasAttribute('title')) anchor.setAttribute('title', window.SiteLanguage?.t(savedTitle) || savedTitle);
    anchor = null; savedTitle = null;
  }
  function position() {
    const rect = anchor.getBoundingClientRect(), margin = 8, gap = 8;
    const width = document.documentElement.clientWidth || window.innerWidth;
    const height = window.innerHeight;
    let left = rect.left + (rect.width - tip.offsetWidth) / 2;
    let top = rect.bottom + gap;
    if (anchor.closest('.test-ui-nav') && !document.body.classList.contains('dock-expanded')) {
      left = rect.right + gap; top = rect.top + (rect.height - tip.offsetHeight) / 2;
    } else if (top + tip.offsetHeight > height - margin) top = rect.top - tip.offsetHeight - gap;
    tip.style.left = `${Math.max(margin, Math.min(left, width - tip.offsetWidth - margin))}px`;
    tip.style.top = `${Math.max(margin, Math.min(top, height - tip.offsetHeight - margin))}px`;
  }
  function show(control) {
    hide();
    if (!control.isConnected || candidate(control) !== control) return;
    anchor = control; savedTitle = control.getAttribute('title'); savedLabel = control.getAttribute('aria-label');
    tip.textContent = window.SiteLanguage?.t(label(control)) || label(control);
    // A modal's descendants remain accessible while the rest of the page is inert.
    (control.closest('dialog[open],[popover]') || document.body).append(tip);
    tip.hidden = false; tip.showPopover?.(); position();
    const ids = new Set((control.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
    ids.add(tip.id); control.setAttribute('aria-describedby', [...ids].join(' '));
    if (savedTitle) control.removeAttribute('title');
  }
  function scheduleHide() { clearTimeout(hideTimer); hideTimer = setTimeout(hide, 100); }
  document.addEventListener('pointerover', event => {
    if (event.pointerType === 'touch') return;
    if (tip.contains(event.target)) { clearTimeout(hideTimer); return; }
    const control = candidate(event.target);
    if (!control) return;
    clearTimeout(hideTimer);
    if (control === anchor || control === pending) return;
    hide(); pending = control; showTimer = setTimeout(() => show(control), 250);
  });
  document.addEventListener('pointerout', event => {
    const control = anchor || pending;
    if (control && (control.contains(event.target) || tip.contains(event.target)) && !control.contains(event.relatedTarget) && !tip.contains(event.relatedTarget)) scheduleHide();
  });
  document.addEventListener('focusin', event => {
    const control = candidate(event.target);
    if (control && !pointerFocus) show(control);
  });
  document.addEventListener('focusout', event => { if (anchor?.contains(event.target)) hide(); });
  document.addEventListener('pointerdown', () => { pointerFocus = true; hide(); }, true);
  document.addEventListener('click', hide, true);
  document.addEventListener('keydown', event => { pointerFocus = false; if (event.key === 'Escape') hide(); });
  document.addEventListener('scroll', () => {
    if (anchor === document.activeElement) position();
    else hide();
  }, true);
  document.addEventListener('site:language', hide);
  for (const name of ['resize', 'hashchange', 'test:navigation', 'blur']) window.addEventListener(name, hide);
  new MutationObserver(() => {
    if (anchor && (!anchor.isConnected || anchor.matches(excluded) || anchor.closest('[hidden],[inert],[aria-hidden="true"]') ||
        anchor.getAttribute('aria-label') !== savedLabel || (anchor.hasAttribute('title') && anchor.getAttribute('title') !== savedTitle) ||
        (anchor.closest('dialog') && !anchor.closest('dialog').open))) hide();
  }).observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['title', 'aria-label', 'hidden', 'inert', 'aria-hidden', 'open', 'aria-expanded']});
})();
