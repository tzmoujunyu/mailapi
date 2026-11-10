(() => {
  const holder = document.getElementById('codex-tip-body');
  class BasicList extends window.EditorjsList {
    static get toolbox() { return super.toolbox.filter(item => item.data.style !== 'checklist'); }
    renderSettings() { return super.renderSettings().slice(0, 2); }
  }
  let editor;
  let uploads = 0;

  async function upload(file) {
    if (!file || file.size > 8 * 1024 * 1024) throw new Error('单张图片最多 8 MB');
    const form = new FormData();
    form.append('image', file);
    uploads++;
    const status = document.getElementById('codex-tip-upload-status');
    status.textContent = '正在处理图片…';
    try {
      const response = await fetch('/api/admin/codex-tip-images', { method: 'POST', body: form, cache: 'no-store' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '图片上传失败');
      return data;
    } catch (error) {
      document.getElementById('codex-tip-error').textContent = error.message;
      throw error;
    } finally {
      uploads--;
      status.textContent = uploads ? '正在处理图片…' : '';
    }
  }

  async function open(tip) {
    if (editor) { await editor.isReady; editor.destroy(); }
    editor = new window.EditorJS({
      holder, minHeight: 160, placeholder: '输入内容，点击 ＋ 插入图片或其他内容',
      data: { blocks: window.CodexTipContent.blocks(tip) },
      inlineToolbar: ['bold', 'italic', 'link'],
      tools: {
        header: { class: window.Header, config: { levels: [2, 3], defaultLevel: 2 }, inlineToolbar: true },
        list: { class: BasicList, config: { defaultStyle: 'unordered', maxLevel: 3 }, inlineToolbar: true },
        code: window.CodeTool,
        image: { class: window.ImageTool, config: {
          types: 'image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif,image/avif',
          features: { border: false, background: false, stretch: false, caption: 'optional' },
          buttonContent: '选择图片', captionPlaceholder: '图片说明',
          uploader: { uploadByFile: upload, uploadByUrl: () => Promise.reject(new Error('请上传或粘贴图片文件')) },
        } },
      },
      i18n: { messages: {
        toolNames: { Text: '段落', Heading: '小标题', List: '列表', 'Ordered List': '有序列表', 'Unordered List': '无序列表',
          Code: '代码', Image: '图片', Bold: '加粗', Italic: '斜体', Link: '链接' },
        blockTunes: { delete: { Delete: '删除', 'Click to delete': '确认删除' }, moveUp: { 'Move up': '上移' }, moveDown: { 'Move down': '下移' } },
      } },
    });
    await editor.isReady;
  }

  async function save() {
    if (uploads) throw new Error('请等待图片上传完成');
    await editor.isReady;
    const data = await editor.save();
    return data.blocks;
  }

  window.CodexTipEditor = { open, save, busy: () => uploads > 0 };
})();
