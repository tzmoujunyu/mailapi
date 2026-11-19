(() => {
  const actions = document.querySelector('.test-ui-header-actions');
  if (!actions) return;
  const translate = text => window.SiteLanguage?.t(text) || text;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'header-icon';
  button.id = 'header-notifications';
  button.setAttribute('aria-label', '网站公告');
  button.setAttribute('aria-expanded', 'false');
  button.setAttribute('aria-controls', 'header-notice-panel');
  button.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24"><use href="/static/test-navigation.svg#bell"></use></svg><span class="notification-count" hidden></span>';
  actions.prepend(button);
  const panel = document.createElement('section');
  panel.id = 'header-notice-panel';
  panel.className = 'header-notice-panel';
  panel.hidden = true;
  panel.setAttribute('aria-label', '通知中心');
  panel.innerHTML = '<div class="notice-panel-heading"><h2>网站公告</h2><button type="button" data-refresh>刷新</button></div><div class="notice-panel-actions"><button type="button" data-read-all>全部已读</button><button type="button" data-create hidden>发布公告</button></div><p role="status" class="notice-error"></p><div class="notice-items"></div>';
  actions.append(panel);
  const dialog = document.createElement('dialog');
  dialog.className = 'site-announcement-dialog';
  dialog.setAttribute('aria-labelledby', 'site-announcement-heading');
  dialog.innerHTML = '<div class="notice-panel-heading"><h2 id="site-announcement-heading">公告详情</h2><button type="button" data-close aria-label="关闭">×</button></div><article class="announcement-detail" hidden><h3 data-title data-i18n-skip></h3><time></time><p data-body data-i18n-skip></p></article><form hidden><label>公告标题<input name="title" maxlength="120" required></label><label>公告内容<textarea name="body" maxlength="5000" rows="10" required></textarea></label><label class="notice-pinned"><input name="pinned" type="checkbox">置顶</label><button type="submit" class="primary">保存公告</button></form><p role="status" class="notice-error"></p>';
  document.body.append(dialog);
  const status = panel.querySelector('[role="status"]');
  const dialogStatus = dialog.querySelector('[role="status"]');
  const form = dialog.querySelector('form');
  let items = [], canManage = false, editing = null, lastLoaded = 0, loading = null;
  async function api(url, options = {}) {
    const response = await fetch(url, {cache: 'no-store', ...options});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '操作失败，请重试');
    return data;
  }
  const json = (method, value) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
  function setOpen(open) {
    panel.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
  }
  function render() {
    const unread = items.filter(item => item.unread).length;
    const count = button.querySelector('.notification-count');
    count.textContent = unread > 99 ? '99+' : unread;
    count.hidden = unread === 0;
    panel.querySelector('[data-create]').hidden = !canManage;
    panel.querySelector('[data-read-all]').disabled = unread === 0;
    const list = panel.querySelector('.notice-items');
    list.replaceChildren();
    if (!items.length) { list.textContent = translate('暂无网站公告。'); return; }
    for (const item of items) {
      const row = document.createElement('article');
      row.className = 'site-notice-row';
      const read = document.createElement('button');
      read.type = 'button';
      read.className = 'site-notice-title';
      const title = document.createElement('strong');
      title.setAttribute('data-i18n-skip', '');
      title.textContent = item.title;
      read.append(title);
      if (item.unread) {
        const dot = document.createElement('span');
        dot.className = 'notice-unread-dot';
        dot.setAttribute('aria-label', '未读');
        read.append(dot);
      }
      const date = document.createElement('small');
      date.textContent = (item.pinned ? translate('置顶') + ' · ' : '') + new Date(item.updated_at).toLocaleString(document.documentElement.lang);
      read.append(date);
      read.addEventListener('click', () => openDetail(item));
      row.append(read);
      if (canManage) {
        const controls = document.createElement('div');
        controls.className = 'site-notice-controls';
        for (const [label, handler] of [['编辑', () => openEditor(item)], ['删除', () => remove(item)]]) {
          const control = document.createElement('button');
          control.type = 'button';
          control.textContent = translate(label);
          control.addEventListener('click', handler);
          controls.append(control);
        }
        row.append(controls);
      }
      list.append(row);
    }
  }
  async function load() {
    if (loading) return loading;
    loading = api('/api/site-announcements').then(data => {
      items = data.items;
      canManage = data.can_manage;
      lastLoaded = Date.now();
      status.textContent = '';
      render();
    }).catch(error => { status.textContent = error.message; }).finally(() => { loading = null; });
    return loading;
  }
  async function markRead(values) {
    for (let offset = 0; offset < values.length; offset += 100) {
      await api('/api/site-announcements/read', json('POST', {items: values.slice(offset, offset + 100).map(({id, revision}) => ({id, revision}))}));
    }
    await load();
  }
  function prepareDialog() {
    setOpen(false);
    dialogStatus.textContent = '';
    dialog.showModal();
  }
  function openDetail(item) {
    dialog.querySelector('form').hidden = true;
    dialog.querySelector('article').hidden = false;
    dialog.querySelector('[data-title]').textContent = item.title;
    dialog.querySelector('[data-body]').textContent = item.body;
    dialog.querySelector('time').textContent = new Date(item.updated_at).toLocaleString(document.documentElement.lang);
    prepareDialog();
    markRead([item]).catch(error => { dialogStatus.textContent = error.message; });
  }
  function openEditor(item = null) {
    editing = item;
    form.reset();
    form.hidden = false;
    dialog.querySelector('article').hidden = true;
    form.elements.title.value = item?.title || '';
    form.elements.body.value = item?.body || '';
    form.elements.pinned.checked = item?.pinned || false;
    prepareDialog();
    form.elements.title.focus();
  }
  async function remove(item) {
    if (!confirm(translate('删除这条网站公告？'))) return;
    try { await api('/api/admin/site-announcements/' + encodeURIComponent(item.id), {method: 'DELETE'}); await load(); }
    catch (error) { status.textContent = error.message; }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const value = {title: form.elements.title.value, body: form.elements.body.value, pinned: form.elements.pinned.checked};
      if (editing) value.revision = editing.revision;
      await api('/api/admin/site-announcements' + (editing ? '/' + encodeURIComponent(editing.id) : ''), json(editing ? 'PUT' : 'POST', value));
      dialog.close();
      await load();
    } catch (error) { dialogStatus.textContent = error.message; }
    finally { submit.disabled = false; }
  });
  button.addEventListener('click', () => { setOpen(panel.hidden); if (!panel.hidden && Date.now() - lastLoaded > 300000) load(); });
  panel.querySelector('[data-refresh]').addEventListener('click', load);
  panel.querySelector('[data-create]').addEventListener('click', () => openEditor());
  panel.querySelector('[data-read-all]').addEventListener('click', () => markRead(items.filter(item => item.unread)).catch(error => { status.textContent = error.message; }));
  dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => { form.reset(); dialog.querySelector('[data-body]').textContent = ''; button.focus(); });
  document.addEventListener('click', event => { if (!panel.contains(event.target) && !button.contains(event.target)) setOpen(false); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) { setOpen(false); button.focus(); } });
  document.addEventListener('site:language', render);
  load();
})();
