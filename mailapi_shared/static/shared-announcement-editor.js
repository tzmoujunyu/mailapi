(() => {
  const scripts = new Map(), readers = new WeakMap(), galleries = new WeakSet();
  const t = text => window.SiteLanguage?.t(text) || text;
  function script(name, ready) {
    if (ready()) return Promise.resolve();
    if (!scripts.has(name)) {
      const node = document.createElement('script'); node.src = '/static/' + name;
      scripts.set(name, new Promise((resolve, reject) => {
        node.onload = resolve;
        node.onerror = () => { scripts.delete(name); node.remove(); reject(new Error(t('编辑器加载失败，请关闭后重试。'))); };
        document.head.append(node);
      }));
    }
    return scripts.get(name);
  }
  function style(href) {
    if ([...document.querySelectorAll('link[rel="stylesheet"]')].some(node => node.getAttribute('href') === href && !node.hasAttribute('data-test-page-style'))) return;
    const node = document.createElement('link'); node.rel = 'stylesheet'; node.href = href; document.head.append(node);
  }
  async function reader() {
    style('/static/codex-tips.css');
    await script('codex-tip-content.js', () => window.CodexTipContent);
  }
  async function dependencies() {
    await reader();
    await script('codex-tip-format.js', () => window.CodexTipFormat);
    await script('vendor/tiptap/tiptap.js', () => window.MailapiTiptap);
    await script('shared-content-editor.js', () => window.SharedContentEditor);
    await script('shared-content-dialog.js', () => window.SharedContentDialog);
  }
  async function read(item, root, dialog) {
    const token = {}; readers.set(root, token);
    root.textContent = item.body || ''; root.classList.add('codex-tip-article');
    await reader();
    if (!root.isConnected || readers.get(root) !== token) return;
    window.CodexTipContent.render(item, root);
    if (!root.querySelector('[data-tip-image]')) return;
    style('/static/vendor/photoswipe/photoswipe.css');
    await script('vendor/photoswipe/photoswipe.js', () => window.PhotoSwipe);
    if (!root.isConnected || readers.get(root) !== token || galleries.has(root)) return;
    window.CodexTipContent.imageGallery(root, dialog); galleries.add(root);
  }
  function create({ scope, container = document.body, save, saved = () => {} }) {
    if (!['site', 'finance'].includes(scope)) throw new Error('不支持的公告范围');
    const prefix = scope + '-notice';
    let dialog, editor, editing, opening = null;
    const find = suffix => dialog.querySelector(`[id="${prefix}-${suffix}"]`);
    function initialize() {
      dialog = window.SharedContentDialog.create({ prefix }); container.append(dialog);
      const pinned = document.createElement('label'); pinned.className = 'checkbox-label shared-content-pinned';
      pinned.innerHTML = `<input id="${prefix}-pinned" type="checkbox"><span>${t('置顶')}</span>`;
      find('title').closest('label').after(pinned);
      editor = window.SharedContentEditor.create({ dialog, prefix, uploadURL: '/api/announcement-images/' + scope,
        imagePrefix: '/api/content-images/' + scope + '/', extraFingerprint: () => find('pinned').checked });
      find('cancel').addEventListener('click', () => editor.close());
      find('form').addEventListener('submit', async event => {
        event.preventDefault(); if (find('save').disabled || find('form').inert) return;
        find('save').disabled = find('cancel').disabled = true; find('form').inert = true; find('error').textContent = '';
        try {
          const value = { title: find('title').value, blocks: await editor.save(), doc_version: 1, pinned: find('pinned').checked };
          if (editing) value.revision = editing.revision;
          await save(value, editing);
          editor.markSaved(); dialog.close(); await saved();
        } catch (error) { find('error').textContent = error.message; }
        finally { find('save').disabled = find('cancel').disabled = false; find('form').inert = false; }
      });
    }
    async function open(item = null) {
      if (opening) return opening;
      opening = (async () => {
        await dependencies(); if (!dialog) initialize();
        if (dialog.open && !await editor.canLeave()) return;
        editing = item; find('title').value = item?.title || ''; find('pinned').checked = !!item?.pinned;
        find('edit-heading').textContent = t(item ? '编辑公告' : '发布公告'); find('error').textContent = '';
        await editor.open(item); dialog.showModal(); find('title').focus();
      })();
      try { await opening; } finally { opening = null; }
    }
    return { open, async canLeave() { if (opening) { try { await opening; } catch {} } if (!editor) return true; const allowed = await editor.canLeave(); if (allowed && dialog.open) dialog.close(); return allowed; },
      get dialog() { return dialog; }, destroy() { editor?.destroy(); dialog?.remove(); } };
  }
  window.SharedAnnouncementEditor = { create, read };
})();
