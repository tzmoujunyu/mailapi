(() => {
  const { Editor, Node: TipNode, StarterKit, ListItem, FileHandler } = window.MailapiTiptap;
  const holder = document.getElementById('codex-tip-body');
  const toolbar = document.getElementById('codex-tip-toolbar');
  const picker = document.getElementById('codex-tip-image-file');
  const linkPanel = document.getElementById('codex-tip-link-panel');
  const linkInput = document.getElementById('codex-tip-link-url');
  const error = document.getElementById('codex-tip-error');
  const uploadStatus = document.getElementById('codex-tip-upload-status');
  let editor;
  let uploads = 0;

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
        if (!/^\/api\/codex-tip-images\/[0-9a-f]{32}\.webp$/.test(url || '')) return false;
        return { file: { url, preview: url.replace('.webp', '.preview.webp'),
          width: Number(node.querySelector('img')?.getAttribute('width')), height: Number(node.querySelector('img')?.getAttribute('height')) } };
      } }];
    },
    renderHTML({ node }) {
      const file = node.attrs.file || {};
      return ['figure', { 'data-codex-image': file.url },
        ['img', { src: file.url, width: file.width, height: file.height, alt: '提示图片', contenteditable: 'false' }],
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
    const checks = { bold: 'bold', italic: 'italic', link: 'link', bulletList: 'bulletList', orderedList: 'orderedList', codeBlock: 'codeBlock' };
    for (const button of toolbar.querySelectorAll('[data-tip-action]')) {
      const action = button.dataset.tipAction;
      if (checks[action]) button.setAttribute('aria-pressed', String(editor.isActive(checks[action])));
      button.disabled = action === 'undo' ? !editor.can().undo() : action === 'redo' ? !editor.can().redo()
        : action === 'image' ? uploads > 0 : action === 'codeBlock' ? editor.isActive('tipImage') || !editor.can().toggleCodeBlock()
        : ['bulletList', 'orderedList'].includes(action) ? editor.isActive('tipImage') || !editor.can()[`toggle${action[0].toUpperCase() + action.slice(1)}`]() : false;
    }
    const format = toolbar.querySelector('select');
    format.value = editor.isActive('heading', { level: 2 }) ? 'h2' : editor.isActive('heading', { level: 3 }) ? 'h3' : 'paragraph';
    format.disabled = editor.isActive('listItem') || editor.isActive('tipImage') || editor.isActive('codeBlock');
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
          const response = await fetch('/api/admin/codex-tip-images', { method: 'POST', body: form, cache: 'no-store' });
          const data = await response.json();
          if (!response.ok || data.success !== 1 || !/^\/api\/codex-tip-images\/[0-9a-f]{32}\.webp$/.test(data.file?.url || '')) throw new Error(typeof data.detail === 'string' ? data.detail : '图片上传失败');
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
    if (action === 'link') {
      linkPanel.hidden = !linkPanel.hidden;
      if (!linkPanel.hidden) { linkInput.value = editor.getAttributes('link').href || ''; linkInput.focus(); }
      return;
    }
    const commands = { bold: 'toggleBold', italic: 'toggleItalic', bulletList: 'toggleBulletList', orderedList: 'toggleOrderedList',
      codeBlock: 'toggleCodeBlock', undo: 'undo', redo: 'redo' };
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

  async function open(tip) {
    if (editor) editor.destroy();
    editor = null;
    holder.replaceChildren();
    linkPanel.hidden = true;
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
      editorProps: { attributes: { class: 'codex-tip-article codex-tip-writing', role: 'textbox', 'aria-multiline': 'true', 'aria-labelledby': 'codex-tip-body-label' } },
      onSelectionUpdate: updateToolbar, onTransaction: updateToolbar,
    });
    updateToolbar();
  }

  async function save() {
    if (uploads) throw new Error('请等待图片上传完成');
    return window.CodexTipFormat.fromDocument(editor.getJSON());
  }

  window.CodexTipEditor = { open, save, busy: () => uploads > 0 };
})();
