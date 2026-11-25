(() => {
  function create({ prefix, heading = '发布公告', saveLabel = '保存' }) {
    if (!/^[a-z][a-z0-9-]+$/.test(prefix)) throw new Error('无效的编辑器标识');
    const dialog = document.createElement('dialog');
    dialog.id = `${prefix}-edit-dialog`;
    dialog.setAttribute('aria-labelledby', `${prefix}-edit-heading`);
    dialog.className = 'shared-content-edit-dialog';
    dialog.innerHTML = `
      <form id="codex-tip-form" class="password-form">
        <div class="codex-tip-editor-heading">
          <h2 id="codex-tip-edit-heading">新增提示</h2>
          <div class="actions">
            <button id="codex-tip-expand" class="btn" type="button" aria-pressed="false">展开编辑器</button>
            <button id="codex-tip-close" class="btn" type="button" aria-label="关闭编辑器" title="关闭编辑器"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button>
          </div>
        </div>
        <label class="codex-tip-title-field">标题<input id="codex-tip-title" maxlength="120" placeholder="输入清晰、简短的标题" required /></label>
        <div class="codex-tip-editor-field">
          <div class="codex-tip-editor-modes"><label id="codex-tip-body-label">正文</label><div role="group" aria-label="内容视图"><button type="button" data-tip-mode="edit" aria-pressed="true">编辑</button><button type="button" data-tip-mode="preview" aria-pressed="false">阅读预览</button></div></div>
          <div class="codex-tip-editor-shell">
            <div id="codex-tip-toolbar" class="codex-tip-toolbar" role="toolbar" aria-label="正文格式">
              <select aria-label="段落格式"><option value="paragraph">正文</option><option value="h2">小标题</option><option value="h3">次级标题</option></select>
              <span class="tip-toolbar-group"><button type="button" data-tip-action="bold" aria-label="加粗" title="加粗（Ctrl / ⌘ B）" aria-pressed="false"><b>B</b></button><button type="button" data-tip-action="italic" aria-label="斜体" title="斜体（Ctrl / ⌘ I）" aria-pressed="false"><i>I</i></button><button type="button" data-tip-action="link" aria-pressed="false">链接</button><button type="button" data-tip-action="code" aria-pressed="false">行内代码</button></span>
              <span class="tip-toolbar-group"><button type="button" data-tip-action="bulletList" aria-pressed="false" title="无序列表">• 列表</button><button type="button" data-tip-action="orderedList" aria-pressed="false" title="有序列表">1. 列表</button><button type="button" data-tip-action="codeBlock" aria-pressed="false">代码块</button></span>
              <span class="tip-toolbar-group"><button type="button" data-tip-action="image">＋ 图片</button><button type="button" data-tip-action="clear">清除格式</button><button type="button" data-tip-action="undo" aria-label="撤销" title="撤销（Ctrl / ⌘ Z）">↶</button><button type="button" data-tip-action="redo" aria-label="重做" title="重做">↷</button></span>
            </div>
            <div id="codex-tip-link-panel" class="codex-tip-link-panel" hidden>
              <label>链接地址<input id="codex-tip-link-url" type="text" inputmode="url" autocomplete="off" placeholder="https://…" /></label>
              <button class="btn" type="button" data-link-action="save">应用</button><button class="btn" type="button" data-link-action="remove">移除链接</button><button class="btn" type="button" data-link-action="cancel">取消</button>
            </div>
            <div id="codex-tip-image-tools" class="codex-tip-image-tools" role="group" aria-label="图片操作" hidden><span>已选中图片</span><button class="btn" type="button" data-tip-image-action="before">在图前写字</button><button class="btn" type="button" data-tip-image-action="after">在图后写字</button><button class="btn btn-danger" type="button" data-tip-image-action="delete">删除图片</button></div>
            <div id="codex-tip-body" aria-labelledby="codex-tip-body-label"></div>
            <div id="codex-tip-preview" class="codex-tip-article" role="region" aria-label="阅读预览" hidden></div>
          </div>
          <input id="codex-tip-image-file" type="file" accept="image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif,image/avif" multiple hidden />
        </div>
        <div class="codex-tip-editor-footer">
          <p class="muted codex-tip-editor-help">支持粘贴或拖入图片；选中图片可在前后写字。单张最多 8 MB，动态图保存首帧。</p>
          <p id="codex-tip-upload-status" class="muted" role="status"></p>
          <p id="codex-tip-error" class="bad" role="alert"></p>
          <div id="codex-tip-leave-warning" role="alert" hidden><span>修改尚未保存，确定放弃吗？</span><button class="btn" id="codex-tip-keep-editing" type="button">继续编辑</button><button class="btn btn-danger" id="codex-tip-discard" type="button">放弃修改</button></div>
          <div class="codex-tip-editor-bottom"><div class="codex-tip-editor-status"><span id="codex-tip-save-state" role="status"></span><span id="codex-tip-word-count"></span></div><div class="dialog-actions"><button id="codex-tip-cancel" class="btn" type="button">取消</button><button id="codex-tip-save" class="btn btn-primary" type="submit" title="保存（Ctrl / ⌘ S）">保存</button></div></div>
        </div>
      </form>
    `.replaceAll('id="codex-tip-', `id="${prefix}-`).replaceAll('aria-labelledby="codex-tip-', `aria-labelledby="${prefix}-`);
    const find = suffix => dialog.querySelector(`[id="${prefix}-${suffix}"]`);
    for (const suffix of ['form', 'close', 'leave-warning', 'upload-status', 'error', 'body', 'preview']) find(suffix).classList.add(`shared-content-${suffix}`);
    find('edit-heading').textContent = heading;
    find('save').textContent = saveLabel;
    return dialog;
  }
  window.SharedContentDialog = { create };
})();
