(() => {
  const root = document.documentElement;
  const catalog = window.MILVLG_EN;
  const reverse = new Map();
  for (const [source, translated] of Object.entries(catalog)) if (!reverse.has(translated)) reverse.set(translated, source);
  const remembered = new WeakMap();
  const attributes = new WeakMap();
  let language = root.dataset.interfaceLanguage === 'en' ? 'en' : 'zh-CN';
  if (!root.dataset.themeAccount) {
    try { language = localStorage.getItem('milvlg.signin.language') === 'en' ? 'en' : language; } catch {}
  }
  const templates = [
    [/^查看全部 (\d+) 位成员$/, n => `View all ${n} members`],
    [/^确认删除 Coding Plan“(.+)”吗？$/, name => `Delete Coding Plan “${name}”?`],
    [/^确认删除中转站账号“(.+)”吗？$/, name => `Delete relay account “${name}”?`],
    [/^确认删除收件设置“(.+)”吗？$/, name => `Delete mail rule “${name}”?`],
    [/^确认删除 (.+) 的 (2FA 密钥|GPT 密码)吗？$/, (name, field) => `Delete the ${t(field)} for ${name}?`],
    [/^确认删除 GPT 账号 (.+)、登录信息及其 Codex 授权？关联的邮箱仍可在邮箱池中管理。$/, name => `Delete GPT account ${name}, its login details and Codex authorization? Linked mailboxes remain in the mail pool.`],
    [/^删除提示「(.+)」？$/, title => `Delete tip “${title}”?`],
    [/^将网站保存的「(.+)」恢复到这次修改前？$/, field => `Restore “${t(field)}” to its value before this change?`],
    [/^删除分组“(.+)”？成员通过此分组获得的资源权限将立即取消。$/, name => `Delete group “${name}”? Its members will immediately lose the resource access granted by this group.`],
    [/^已匹配 (\d+) 个账户，并标记为需修改密码。$/, n => `${n} matching accounts marked for a password change.`],

    [/^组(\d+)$/, n => `Group ${n}`],
    [/^(.+) · (\d+)$/, (label, n) => `${t(label)} · ${n}`],
    [/^编辑账户 · (.+)$/, name => `Edit account · ${name}`],
    [/^编辑(?:小组|分组) · (.+)$/, name => `Edit group · ${name}`],
    [/^编辑小组、成员与资源 · (.+)$/, name => `Edit group, members and resources · ${name}`],
    [/^删除分组 · (.+)$/, name => `Delete group · ${name}`],
    [/^向 (.+) 添加小组$/, name => `Add a group to ${name}`],
    [/^编辑 (.+)$/, name => `Edit ${name}`],
    [/^删除 (.+)$/, name => `Delete ${name}`],
    [/^创建(?:小组|分组)$/, () => 'Create group'],
    [/^共 (\d+) 项匹配，可点击下方模块查看$/, n => `${n} matches. Select a section below.`],
    [/^匹配 (\d+) \/ (\d+) 个资源$/, (n, total) => `${n} of ${total} resources match`],
    [/^共 (\d+) 个资源$/, n => `${n} resources`],
    [/^已选 (\d+) \/ (\d+) 个分组$/, (n, total) => `${n} of ${total} groups selected`],
    [/^已添加 (\d+) 个账户$/, n => `${n} accounts added`],
    [/^新 Key（只显示一次）：(.+)$/, key => `New key (shown once): ${key}`],
    [/^请求 (\d+) 次 · 输入 (\d+) · 输出 (\d+) · 总计 (\d+) Token$/, (n, input, output, total) => `${n} requests · ${input} input · ${output} output · ${total} tokens`],
    [/^(\d+)天$/, n => `${n} days`],
    [/^(\d+)min后刷新$/, n => `Refresh in ${n} min`],
    [/^更新于 (.+)$/, date => `Updated ${date}`],
    [/^订阅到期：(.+)$/, value => `Subscription expiry: ${value}`],
    [/^订阅开通：(.+)$/, value => `Subscription start: ${value}`],
    [/^授权上次核对：(.+)$/, value => `Authorization last checked: ${value}`],
    [/^当前：(.+)，点击切换为(.+)$/, (current, next) => `Current: ${t(current)}. Switch to ${t(next)}`],
    [/^当前：(.+)$/, current => `Current: ${t(current)}`],
    [/^点击切换为(.+)$/, next => `Switch to ${t(next)}`],
    [/^账户菜单：(.+)$/, name => `Account menu: ${name}`],
  ];
  function t(text) {
    if (language !== 'en') return reverse.get(text) || text;
    if (Object.hasOwn(catalog, text)) return catalog[text];
    for (const [pattern, replace] of templates) {
      const match = text.match(pattern);
      if (match) return replace(...match.slice(1));
    }
    return text;
  }
  const protectedContent = '[data-i18n-skip],script,style,code,pre,[contenteditable],.tiptap,.codex-tip-content,.codex-tip-article,#codex-tip-detail-title,.resource-name,.account-email,.tree-account-name,[data-identity],[data-groups],[data-username],#new-api-key';
  function protectedNode(element) {
    if (element.closest(protectedContent)) return true;
    if (!element.closest('button:not(.matrix-edit),[data-ui-text]') && element.closest('.option > span,.mailbox-option strong,.mailbox-option .muted,.tree-item-sub,.matrix-members,.matrix-account,.activity-list,#api-key-list')) return true;
    if (element.closest('datalist')) return true;
    if (element.matches('option:not([data-ui-text])') && element.value && !['user','manager','admin','gmail','outlook','code','link','auto','all','any','contains','not_contains','regex','exact','domain','subject','body','sender'].includes(element.value)) return true;
    if (element.closest('tbody') && !element.closest('button:not(.matrix-edit),.btn,[data-ui-text],.status-pill,.status-tag,.plan-pill,.expiry-tag,.matrix-add-label,.subscription-time,.status-good,.status-warn,.status-bad,.good,.warn,.bad,.plan,.quota-meter,.account-main + .muted,.account-line + .muted') && !element.closest('td[colspan]')) return true;
    return false;
  }
  function translateText(node) {
    const element = node.parentElement;
    if (!element || protectedNode(element) || !node.data.trim()) return;
    let saved = remembered.get(node);
    if (!saved || saved.rendered !== node.data) saved = {source: node.data, rendered: node.data};
    const core = saved.source.trim();
    saved.rendered = saved.source.replace(core, () => t(core));
    remembered.set(node, saved);
    if (node.data !== saved.rendered) node.data = saved.rendered;
  }
  function translateAttributes(element) {
    if (element.closest('[data-i18n-skip]')) return;
    const saved = attributes.get(element) || {};
    for (const key of ['title', 'placeholder', 'aria-label']) {
      if (!element.hasAttribute(key)) continue;
      const value = element.getAttribute(key);
      if (!saved[key] || saved[key].rendered !== value) saved[key] = {source: value, rendered: value};
      saved[key].rendered = t(saved[key].source);
      if (value !== saved[key].rendered) element.setAttribute(key, saved[key].rendered);
    }
    attributes.set(element, saved);
  }
  function scan(node) {
    if (node.nodeType === Node.TEXT_NODE) return translateText(node);
    if (node.nodeType !== Node.ELEMENT_NODE && node.nodeType !== Node.DOCUMENT_NODE) return;
    if (node.nodeType === Node.ELEMENT_NODE) translateAttributes(node);
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let current;
    while ((current = walker.nextNode())) {
      if (current.nodeType === Node.TEXT_NODE) translateText(current);
      else translateAttributes(current);
    }
  }
  function apply(value) {
    language = value === 'en' ? 'en' : 'zh-CN';
    root.lang = language;
    root.dataset.interfaceLanguage = language;
    scan(document);
    document.dispatchEvent(new CustomEvent('site:language', {detail: {language}}));
  }
  async function set(value) {
    const previous = language;
    apply(value);
    try {
      if (root.dataset.themeAccount) {
        const response = await fetch('/api/test-ui/preferences', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({language})});
        if (!response.ok) throw new Error('语言保存失败，请重试');
      }
      try { localStorage.setItem('milvlg.signin.language', language); } catch {}
    } catch (error) { apply(previous); throw error; }
  }
  function mount(holder) {
    const control = document.createElement('div');
    control.className = 'site-language';
    control.innerHTML = '<button type="button" class="header-icon" aria-label="切换语言" aria-expanded="false"><svg aria-hidden="true" viewBox="0 0 24 24"><use href="/static/test-navigation.svg#languages"></use></svg></button><div class="language-options" role="group" aria-label="切换语言" hidden><button type="button" data-language="zh-CN">简体中文</button><button type="button" data-language="en">English</button><p role="status"></p></div>';
    const button = control.firstElementChild, panel = control.lastElementChild;
    holder.insertBefore(control, holder.querySelector('#header-appearance'));
    function render() {
      for (const option of panel.querySelectorAll('[data-language]')) option.setAttribute('aria-pressed', String(option.dataset.language === language));
    }
    function close() { panel.hidden = true; button.setAttribute('aria-expanded', 'false'); }
    button.addEventListener('click', () => { panel.hidden = !panel.hidden; button.setAttribute('aria-expanded', String(!panel.hidden)); });
    panel.addEventListener('click', async event => {
      const option = event.target.closest('[data-language]');
      if (!option || option.disabled) return;
      const options = [...panel.querySelectorAll('button')];
      options.forEach(item => { item.disabled = true; });
      panel.querySelector('[role="status"]').textContent = '';
      try { await set(option.dataset.language); close(); }
      catch (error) { panel.querySelector('[role="status"]').textContent = t(error.message); }
      finally { options.forEach(item => { item.disabled = false; }); render(); }
    });
    document.addEventListener('click', event => { if (!control.contains(event.target)) close(); });
    control.addEventListener('keydown', event => { if (event.key === 'Escape') { close(); button.focus(); } });
    document.addEventListener('site:language', render);
    render();
  }
  window.SiteLanguage = {t, set, mount};
  for (const method of ['alert', 'confirm', 'prompt']) {
    const native = window[method].bind(window);
    window[method] = (message, ...args) => native(t(message), ...args);
  }
  const queued = new Set();
  let scheduled = false;
  const observer = new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'childList') for (const node of record.addedNodes) queued.add(node);
      else queued.add(record.target);
    }
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      const nodes = [...queued];
      queued.clear();
      for (const node of nodes) if (node.isConnected) scan(node);
    });
  });
  observer.observe(document.documentElement, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['title','placeholder','aria-label']});
  apply(language);
  const loginControls = document.querySelector('.login-theme-header');
  if (loginControls) mount(loginControls);
})();
