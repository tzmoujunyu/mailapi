(() => {
  function create({ dialog, prefix = 'codex-tip', uploadURL = '/api/admin/codex-tip-images',
    imagePrefix = '/api/codex-tip-images/', extraFingerprint = () => null, contextWindow = dialog.ownerDocument.defaultView }) {
  if (!['/api/codex-tip-images/', '/api/content-images/site/', '/api/content-images/finance/'].includes(imagePrefix)) throw new Error('不支持的图片范围');
  const document = dialog.ownerDocument, window = contextWindow;
  const find = suffix => dialog.querySelector(`[id="${prefix}-${suffix}"]`);
  const validImage = url => typeof url === 'string' && url.startsWith(imagePrefix) && /^[0-9a-f]{32}\.webp$/.test(url.slice(imagePrefix.length));
  const subscriptions = new window.AbortController();
  const { Editor, Node: TipNode, StarterKit, ListItem, FileHandler } = window.MailapiTiptap;
  const holder = find('body');
  const toolbar = find('toolbar');
  const picker = find('image-file');
  const linkPanel = find('link-panel');
  const linkInput = find('link-url');
  const error = find('error');
  const uploadStatus = find('upload-status');
  const form = find('form');
  const title = find('title');
  const preview = find('preview');
  const imageTools = find('image-tools');
  const leaveWarning = find('leave-warning');
  const t = text => window.SiteLanguage?.t(text) || text;
  let editor;
  let uploads = 0;
  let baseline = '', mode = 'edit', leaveDecision;
  const fingerprint = () => JSON.stringify([title.value, editor?.getJSON(), extraFingerprint()]);
  const dirty = () => !!editor && fingerprint() !== baseline;

  function updateStatus() {
    if (!editor) return;
    find('save-state').textContent = t(dirty() ? '有未保存的修改' : '尚无新修改');
    const count = [...editor.getText({ blockSeparator: '\n' }).trim()].length;
    find('word-count').textContent = `${count.toLocaleString()} / 8,000`;
    holder.classList.toggle('is-empty', editor.isEmpty);
  }

  function setMode(value) {
    mode = value;
    const reading = value === 'preview';
    if (reading) {
      window.CodexTipContent.render({ title: title.value, blocks: window.CodexTipFormat.fromDocument(editor.getJSON()) }, preview);
      if (editor.isEmpty) preview.textContent = t('暂无正文，请返回编辑后预览。');
    }
    holder.hidden = reading; preview.hidden = !reading; toolbar.hidden = reading;
    linkPanel.hidden = true;
    for (const button of form.querySelectorAll('[data-tip-mode]')) button.setAttribute('aria-pressed', String(button.dataset.tipMode === value));
    updateToolbar();
  }

  function selectedImage() {
    const { selection } = editor.state;
    if (selection.node?.type.name === 'tipImage') return { node: selection.node, at: selection.from };
    for (let depth = selection.$from.depth; depth > 0; depth--) {
      const node = selection.$from.node(depth);
      if (node.type.name === 'tipImage') return { node, at: selection.$from.before(depth) };
    }
    return null;
  }

  function resolveLeave(value) {
    const decision = leaveDecision; leaveDecision = null; leaveWarning.hidden = true;
    decision?.resolve(value);
  }
  function canLeave() {
    if (!dialog.open) return Promise.resolve(true);
    if (uploads || find('cancel').disabled) {
      error.textContent = t('请等待当前操作完成'); return Promise.resolve(false);
    }
    if (!dirty()) return Promise.resolve(true);
    if (!leaveDecision) {
      let resolve;
      const promise = new Promise(done => { resolve = done; });
      leaveDecision = { promise, resolve }; leaveWarning.hidden = false;
      find('keep-editing').focus();
    }
    return leaveDecision.promise;
  }
  async function close() { if (await canLeave()) dialog.close(); }

  function validURL(value) {
    try { const url = new URL(value); return /^https?:$/.test(url.protocol) && !!url.hostname && !/[\s\x00-\x1f]/.test(value); }
    catch { return false; }
  }

  const TipCaption = TipNode.create({
    name: 'tipCaption', content: 'inline*', defining: true,
    parseHTML() { return [{ tag: 'div[data-tip-caption]' }]; },
    renderHTML() { return ['div', { 'data-tip-caption': '', 'data-placeholder': '添加图片说明（选填）' }, 0]; },
  });
  const TipImage = TipNode.create({
    name: 'tipImage', group: 'block', content: 'tipCaption', isolating: true, draggable: true,
    addAttributes() { return { file: { default: null, rendered: false } }; },
    parseHTML() {
      return [{ tag: 'figure[data-codex-image]', contentElement: 'figcaption', getAttrs: node => {
        const url = node.getAttribute('data-codex-image');
        if (!validImage(url)) return false;
        return { file: { url, preview: url.replace('.webp', '.preview.webp'),
          width: Number(node.querySelector('img')?.getAttribute('width')), height: Number(node.querySelector('img')?.getAttribute('height')) } };
      } }];
    },
    renderHTML({ node }) {
      const file = node.attrs.file || {};
      return ['figure', { 'data-codex-image': file.url },
        ['img', { src: file.url, width: file.width, height: file.height, alt: '内容图片', contenteditable: 'false' }],
        ['figcaption', 0]];
    },
    addKeyboardShortcuts() {
      return { Enter: () => {
        const { $from } = this.editor.state.selection;
        if ($from.parent.type.name !== 'tipCaption') return false;
        const at = $from.after($from.depth - 1);
        const next = this.editor.state.doc.nodeAt(at);
        if (next?.type.name === 'paragraph' && !next.content.size) return this.editor.chain().setTextSelection(at + 1).focus().run();
        return this.editor.chain().insertContentAt(at, { type: 'paragraph' }).focus().run();
      } };
    },
  });

  function updateToolbar() {
    if (!editor) return;
    let containsImage = !!selectedImage();
    editor.state.doc.nodesBetween(editor.state.selection.from, editor.state.selection.to, node => {
      if (node.type.name === 'tipImage') containsImage = true;
    });
    const checks = { bold: 'bold', italic: 'italic', link: 'link', code: 'code', bulletList: 'bulletList', orderedList: 'orderedList', codeBlock: 'codeBlock' };
    for (const button of toolbar.querySelectorAll('[data-tip-action]')) {
      const action = button.dataset.tipAction;
      if (checks[action]) button.setAttribute('aria-pressed', String(editor.isActive(checks[action])));
      button.disabled = action === 'undo' ? !editor.can().undo() : action === 'redo' ? !editor.can().redo()
        : action === 'image' ? uploads > 0 : action === 'clear' ? containsImage : action === 'codeBlock' ? editor.isActive('tipImage') || !editor.can().toggleCodeBlock()
        : ['bulletList', 'orderedList'].includes(action) ? editor.isActive('tipImage') || !editor.can()[`toggle${action[0].toUpperCase() + action.slice(1)}`]() : false;
    }
    const format = toolbar.querySelector('select');
    format.value = editor.isActive('heading', { level: 2 }) ? 'h2' : editor.isActive('heading', { level: 3 }) ? 'h3' : 'paragraph';
    format.disabled = editor.isActive('listItem') || editor.isActive('tipImage') || editor.isActive('codeBlock');
    imageTools.hidden = mode !== 'edit' || !selectedImage();
    for (const button of imageTools.querySelectorAll('button')) button.disabled = uploads > 0;
    form.querySelector('[data-tip-mode="preview"]').disabled = uploads > 0;
  }

  async function uploadFiles(files, position) {
    if (uploads) { error.textContent = '请等待当前图片上传完成后再添加图片'; return; }
    const instance = editor;
    let bookmark = instance.state.selection.getBookmark();
    if (position !== undefined) bookmark = instance.state.selection.constructor.near(instance.state.doc.resolve(position)).getBookmark();
    const map = ({ transaction }) => { bookmark = bookmark.map(transaction.mapping); };
    instance.on('transaction', map);
    uploads = files.length;
    error.textContent = '';
    updateToolbar();
    const images = [];
    try {
      for (const file of files) {
        uploadStatus.textContent = `正在处理图片（${uploads}）…`;
        try {
          if (!file.size || file.size > 8 * 1024 * 1024) throw new Error('单张图片须为 1 字节至 8 MB');
          const form = new FormData();
          form.append('image', file);
          const response = await fetch(uploadURL, { method: 'POST', body: form, cache: 'no-store' });
          const data = await response.json();
          if (!response.ok || data.success !== 1 || !validImage(data.file?.url)) throw new Error(typeof data.detail === 'string' ? data.detail : '图片上传失败');
          images.push({ type: 'tipImage', attrs: { file: data.file }, content: [{ type: 'tipCaption' }] });
        } catch (failure) { error.textContent = failure.message; }
        finally { uploads--; }
      }
      if (images.length && !instance.isDestroyed) {
        const selection = bookmark.resolve(instance.state.doc);
        const at = selection.$to.depth ? selection.$to.after(1) : selection.to;
        instance.chain().insertContentAt(at, [...images, { type: 'paragraph' }]).focus().run();
      }
    } finally {
      instance.off('transaction', map);
      uploads = 0;
      uploadStatus.textContent = '';
      updateToolbar();
    }
  }

  toolbar.addEventListener('mousedown', event => { if (event.target.closest('button')) event.preventDefault(); });
  toolbar.addEventListener('click', event => {
    const button = event.target.closest('[data-tip-action]');
    if (!button || button.disabled || !editor) return;
    const action = button.dataset.tipAction;
    if (action === 'image') { picker.click(); return; }
    if (action === 'clear') { editor.chain().focus().unsetAllMarks().clearNodes().run(); return; }
    if (action === 'link') {
      linkPanel.hidden = !linkPanel.hidden;
      if (!linkPanel.hidden) { linkInput.value = editor.getAttributes('link').href || ''; linkInput.focus(); }
      return;
    }
    const commands = { bold: 'toggleBold', italic: 'toggleItalic', bulletList: 'toggleBulletList', orderedList: 'toggleOrderedList',
      code: 'toggleCode', codeBlock: 'toggleCodeBlock', undo: 'undo', redo: 'redo' };
    editor.chain().focus()[commands[action]]().run();
  });
  toolbar.querySelector('select').addEventListener('change', event => {
    if (event.target.value === 'paragraph') editor.chain().focus().setParagraph().run();
    else editor.chain().focus().setHeading({ level: Number(event.target.value.slice(1)) }).run();
  });
  picker.addEventListener('change', () => { const files = [...picker.files]; picker.value = ''; if (files.length) uploadFiles(files); });
  linkPanel.addEventListener('click', event => {
    const action = event.target.closest('[data-link-action]')?.dataset.linkAction;
    if (!action) return;
    const url = linkInput.value.trim();
    if (action === 'save' && url && !validURL(url)) { error.textContent = '请输入完整的 http:// 或 https:// 链接'; linkInput.focus(); return; }
    if (action === 'save' || action === 'remove') {
      const chain = editor.chain().focus().extendMarkRange('link');
      if (action === 'remove' || !url) chain.unsetLink().run();
      else if (editor.state.selection.empty && !editor.isActive('link')) chain.insertContent({ type: 'text', text: url, marks: [{ type: 'link', attrs: { href: url } }] }).run();
      else chain.setLink({ href: url }).run();
      error.textContent = '';
    } else editor.commands.focus();
    linkPanel.hidden = true;
  });
  linkInput.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); linkPanel.querySelector('[data-link-action="save"]').click(); }
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); linkPanel.hidden = true; editor.commands.focus(); }
  });
  imageTools.addEventListener('mousedown', event => { if (event.target.closest('button')) event.preventDefault(); });
  imageTools.addEventListener('click', event => {
    const action = event.target.closest('[data-tip-image-action]')?.dataset.tipImageAction;
    const image = selectedImage();
    if (!action || !image || uploads) return;
    if (action === 'delete') editor.chain().focus().deleteRange({ from: image.at, to: image.at + image.node.nodeSize }).run();
    else {
      const at = action === 'before' ? image.at : image.at + image.node.nodeSize;
      editor.chain().insertContentAt(at, { type: 'paragraph' }).setTextSelection(at + 1).focus().run();
    }
  });
  for (const button of form.querySelectorAll('[data-tip-mode]')) button.addEventListener('click', () => { if (editor) setMode(button.dataset.tipMode); });
  find('expand').addEventListener('click', event => {
    const expanded = dialog.classList.toggle('is-expanded');
    event.currentTarget.setAttribute('aria-pressed', String(expanded));
    event.currentTarget.textContent = t(expanded ? '还原窗口' : '展开编辑器');
  });
  find('close').addEventListener('click', close);
  find('keep-editing').addEventListener('click', () => resolveLeave(false));
  find('discard').addEventListener('click', () => resolveLeave(true));
  form.addEventListener('input', updateStatus);
  form.addEventListener('change', updateStatus);
  dialog.addEventListener('cancel', event => { event.preventDefault(); if (leaveDecision) resolveLeave(false); else close(); });
  dialog.addEventListener('close', () => resolveLeave(!dirty()));
  dialog.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault(); if (!form.inert) form.requestSubmit();
    }
  });
  window.addEventListener('beforeunload', event => {
    if (dialog.open && (dirty() || uploads || form.inert)) { event.preventDefault(); event.returnValue = ''; }
  }, { signal: subscriptions.signal });
  document.addEventListener('site:language', updateStatus, { signal: subscriptions.signal });

  async function open(tip) {
    if (window.CodexTipContent.blocks(tip).some(block => block.type === 'image' && !validImage(block.data?.file?.url))) throw new Error('正文包含其他内容范围的图片');
    if (editor) editor.destroy();
    editor = null;
    holder.replaceChildren();
    linkPanel.hidden = true;
    mode = 'edit'; resolveLeave(false);
    editor = new Editor({
      element: holder, content: window.CodexTipFormat.toDocument(tip),
      extensions: [StarterKit.configure({ heading: { levels: [2, 3] }, blockquote: false, horizontalRule: false,
        strike: false, underline: false, trailingNode: false, listItem: false,
        link: { openOnClick: false, isAllowedUri: validURL } }),
      ListItem.extend({ content: 'paragraph (bulletList | orderedList)?' }), TipCaption, TipImage,
      FileHandler.configure({ consumePasteEvent: true, allowedMimeTypes: ['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/heic', 'image/heif', 'image/avif'],
        onPaste: (instance, files) => { uploadFiles(files); },
        onDrop: (instance, files, position) => { uploadFiles(files, position); } }),
      ],
      editorProps: {
        attributes: { class: 'codex-tip-article codex-tip-writing', role: 'textbox', 'aria-multiline': 'true', 'aria-labelledby': `${prefix}-body-label` },
        handleClickOn(view, pos, node, nodePos, event) {
          if (node.type.name !== 'tipImage' || event.target.tagName !== 'IMG') return false;
          return editor.chain().setNodeSelection(nodePos).focus().run();
        },
      },
      onSelectionUpdate: updateToolbar, onTransaction: updateToolbar, onUpdate: updateStatus,
    });
    baseline = fingerprint(); setMode('edit'); updateStatus();
  }

  async function save() {
    if (uploads) throw new Error('请等待图片上传完成');
    if ([...editor.getText({ blockSeparator: '\n' }).trim()].length > 8000) throw new Error('正文最多 8000 个字符');
    return window.CodexTipFormat.fromDocument(editor.getJSON());
  }

  return { open, save, close, canLeave, busy: () => uploads > 0,
    destroy() { subscriptions.abort(); editor?.destroy(); resolveLeave(false); },
    markSaved() { baseline = fingerprint(); updateStatus(); resolveLeave(true); } };
  }
  window.SharedContentEditor = { create };
})();
