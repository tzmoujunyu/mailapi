const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '../../mailapi_shared/static');
const asset = { url: '/api/codex-tip-images/' + 'a'.repeat(32) + '.webp',
  preview: '/api/codex-tip-images/' + 'a'.repeat(32) + '.preview.webp', width: 800, height: 400 };

async function page(t, tip = { body: '第一行 <example>\n第二行' }) {
  const html = fs.readFileSync(path.join(root, 'admin.html'), 'utf8');
  const dialog = html.match(/<dialog id="codex-tip-edit-dialog"[\s\S]*?<\/dialog>/)[0];
  const dom = new JSDOM(dialog, { url: 'https://example.test', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  w.matchMedia = () => ({ matches: true });
  w.scrollTo = () => {};
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event('close')); };
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.Range.prototype.getClientRects = () => [];
  w.Range.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 });
  const requests = [];
  const state = { fetch: async () => ({ ok: true, json: async () => ({ success: 1, file: asset }) }) };
  w.fetch = async (url, request) => { requests.push({ url, request }); return state.fetch(); };
  w.eval(fs.readFileSync(path.join(root, 'vendor/tiptap/tiptap.js'), 'utf8'));
  const RealEditor = w.MailapiTiptap.Editor;
  w.MailapiTiptap = { ...w.MailapiTiptap, Editor: class extends RealEditor {
    constructor(options) { super(options); state.editor = this; }
  } };
  for (const script of ['codex-tip-content.js', 'codex-tip-format.js', 'codex-tip-editor.js']) {
    w.eval(fs.readFileSync(path.join(root, script), 'utf8'));
  }
  t.after(() => { state.editor?.destroy(); w.close(); });
  await w.CodexTipEditor.open(tip);
  const button = action => w.document.querySelector(`[data-tip-action="${action}"]`);
  const blocks = async () => JSON.parse(JSON.stringify(await w.CodexTipEditor.save()));
  const paste = () => {
    const event = new w.Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'clipboardData', { value: {
      files: [new w.File(['fixture'], 'picture.png', { type: 'image/png' })], getData: () => '<p>clipboard HTML</p>',
    } });
    state.editor.view.dom.dispatchEvent(event);
    return event;
  };
  async function uploaded() {
    for (let i = 0; i < 100 && w.CodexTipEditor.busy(); i++) await new Promise(resolve => setImmediate(resolve));
    assert.equal(w.CodexTipEditor.busy(), false);
  }
  return { w, state, requests, button, blocks, paste, uploaded };
}

test('real Tiptap preserves literal legacy text and keeps the toolbar outside the scrollable editor', async t => {
  const app = await page(t);
  const blocks = await app.blocks();
  assert.match(blocks[0].data.text, /&lt;example&gt;/);
  assert.match(blocks[0].data.text, /<br>/);
  assert.equal(app.w.document.querySelectorAll('.ProseMirror').length, 1);
  assert.equal(app.w.document.querySelectorAll('#codex-tip-body [role="toolbar"]').length, 0);
  assert.ok(app.w.document.querySelector('[role="toolbar"] button[data-tip-action="bold"]'));
  await app.w.CodexTipEditor.open({ body: '另一个提示' });
  assert.equal(app.w.document.querySelectorAll('.ProseMirror').length, 1);
  assert.equal((await app.blocks())[0].data.text, '另一个提示');
});

test('toolbar formatting, link editing and history operate on the real selection', async t => {
  const app = await page(t, { body: 'hello world' });
  app.state.editor.commands.setTextSelection({ from: 1, to: 6 });
  app.button('bold').click();
  assert.match((await app.blocks())[0].data.text, /<b>hello<\/b>/);
  assert.equal(app.button('bold').getAttribute('aria-pressed'), 'true');
  app.button('italic').click();
  app.button('link').click();
  app.w.document.getElementById('codex-tip-link-url').value = 'https://example.com/docs';
  app.w.document.querySelector('[data-link-action="save"]').click();
  assert.match((await app.blocks())[0].data.text, /href="https:\/\/example.com\/docs"/);
  assert.equal(app.w.document.getElementById('codex-tip-link-panel').hidden, true);
  assert.equal(app.button('undo').disabled, false);
  app.button('undo').click();
  assert.equal(app.button('redo').disabled, false);
  app.button('redo').click();
  const format = app.w.document.querySelector('#codex-tip-toolbar select');
  format.value = 'h2'; format.dispatchEvent(new app.w.Event('change'));
  assert.equal((await app.blocks())[0].type, 'header');
  assert.equal((await app.blocks())[0].data.level, 2);
});

test('existing rich blocks keep headings, code, lists, local images and formatted captions', async t => {
  const tip = { blocks: [
    { type: 'header', data: { text: '步骤', level: 3 } },
    { type: 'list', data: { style: 'unordered', meta: {}, items: [{ content: '<b>第一步</b>', meta: {},
      style: 'ordered', items: [{ content: '第二层', meta: {}, items: [] }] }] } },
    { type: 'code', data: { code: 'x = "<example>"\nprint(x)' } },
    { type: 'image', data: { file: asset, caption: '<b>图注</b><br>第二行' } },
  ] };
  const app = await page(t, tip);
  assert.equal(app.w.document.querySelector('#codex-tip-body img')?.getAttribute('src'), asset.url);
  const blocks = await app.blocks();
  assert.deepEqual(blocks, tip.blocks);
  const article = app.w.document.createElement('div');
  app.w.CodexTipContent.render({ blocks }, article);
  assert.equal(article.querySelectorAll('ul ol').length, 1);
  assert.equal(article.querySelector('img').getAttribute('src'), asset.url);
  assert.equal(article.querySelector('figcaption b').textContent, '图注');
});

test('pasted images use the existing uploader without duplicating clipboard HTML', async t => {
  const app = await page(t);
  assert.equal(app.paste().defaultPrevented, true);
  assert.equal(app.w.CodexTipEditor.busy(), true);
  await assert.rejects(app.w.CodexTipEditor.save(), /上传完成/);
  await app.uploaded();
  assert.equal(app.requests.length, 1);
  assert.equal(app.requests[0].url, '/api/admin/codex-tip-images');
  assert.ok(app.requests[0].request.body.get('image'));
  const blocks = await app.blocks();
  assert.equal(blocks.filter(block => block.type === 'image').length, 1);
  assert.deepEqual(blocks.find(block => block.type === 'image').data.file, asset);
  assert.ok(!JSON.stringify(blocks).includes('clipboard HTML'));
});

test('an upload keeps its insertion location while text changes and failed uploads leave content intact', async t => {
  const app = await page(t, { body: '原文字' });
  let finish;
  app.state.fetch = () => new Promise(resolve => { finish = resolve; });
  app.paste();
  app.state.editor.commands.insertContentAt(1, '新增');
  finish({ ok: true, json: async () => ({ success: 1, file: asset }) });
  await app.uploaded();
  assert.equal((await app.blocks())[0].data.text, '新增原文字');
  assert.equal((await app.blocks())[1].type, 'image');
  const before = await app.blocks();
  app.state.fetch = async () => ({ ok: false, json: async () => ({ detail: '图片无法解析' }) });
  app.paste();
  await app.uploaded();
  assert.deepEqual(await app.blocks(), before);
  assert.match(app.w.document.getElementById('codex-tip-error').textContent, /无法解析/);
});

test('unsafe links and external HTML images are not accepted', async t => {
  const app = await page(t, { body: 'hello' });
  app.state.editor.commands.setTextSelection({ from: 1, to: 6 });
  app.button('link').click();
  app.w.document.getElementById('codex-tip-link-url').value = 'javascript:alert(1)';
  app.w.document.querySelector('[data-link-action="save"]').click();
  assert.match(app.w.document.getElementById('codex-tip-error').textContent, /https/);
  assert.doesNotMatch(JSON.stringify(await app.blocks()), /javascript:/);
  app.state.editor.commands.insertContent('<p>before<img src="https://external.example/image.png">after</p>');
  assert.equal(app.w.document.querySelectorAll('#codex-tip-body img').length, 0);
});

test('ordered list numbering survives conversion and reading', async t => {
  const app = await page(t, { blocks: [{ type: 'list', data: { style: 'ordered', meta: { start: 3, type: 'A' },
    items: [{ content: '步骤', meta: {}, items: [] }] } }] });
  const blocks = await app.blocks();
  assert.deepEqual(blocks[0].data.meta, { start: 3, type: 'A' });
  const article = app.w.document.createElement('div');
  app.w.CodexTipContent.render({ blocks }, article);
  assert.equal(article.querySelector('ol').start, 3);
  assert.equal(article.querySelector('ol').type, 'A');
});

test('preview keeps unsaved formatting and the editor history without uploading or saving', async t => {
  const app = await page(t, { body: 'hello world' });
  app.state.editor.commands.setTextSelection({ from: 1, to: 6 });
  app.button('code').click();
  const before = await app.blocks();
  app.w.document.querySelector('[data-tip-mode="preview"]').click();
  assert.equal(app.w.document.querySelector('#codex-tip-preview code').textContent, 'hello');
  assert.equal(app.w.document.getElementById('codex-tip-body').hidden, true);
  assert.equal(app.w.document.querySelector('#codex-tip-preview [contenteditable]'), null);
  assert.equal(app.requests.length, 0);
  app.w.document.querySelector('[data-tip-mode="edit"]').click();
  assert.deepEqual(await app.blocks(), before);
  app.button('clear').click();
  assert.equal((await app.blocks())[0].data.text, 'hello world');
  app.button('undo').click();
  assert.deepEqual(await app.blocks(), before);
});

test('image controls insert editable paragraphs on either side and removal is undoable', async t => {
  const app = await page(t, { blocks: [{ type: 'image', data: { file: asset, caption: '图注' } }] });
  const selectImage = () => {
    let at;
    app.state.editor.state.doc.descendants((node, pos) => { if (node.type.name === 'tipImage') at = pos; });
    const view = app.state.editor.view;
    assert.equal(view.someProp('handleClickOn', handler => handler(view, at, view.state.doc.nodeAt(at), at,
      {target: app.w.document.querySelector('#codex-tip-body img')}, true)), true);
  };
  const action = name => app.w.document.querySelector(`[data-tip-image-action="${name}"]`).click();
  selectImage();
  assert.equal(app.w.document.getElementById('codex-tip-image-tools').hidden, false);
  assert.equal(app.button('clear').disabled, true);
  action('before'); app.state.editor.commands.insertContent('图前');
  selectImage(); action('after'); app.state.editor.commands.insertContent('图后');
  assert.deepEqual((await app.blocks()).map(block => block.data.text || block.data.caption), ['图前', '图注', '图后']);
  app.state.editor.commands.selectAll();
  assert.equal(app.button('clear').disabled, true, 'clearing mixed text and images must not remove images');
  selectImage(); action('delete');
  assert.equal((await app.blocks()).some(block => block.type === 'image'), false);
  app.button('undo').click();
  assert.equal((await app.blocks()).filter(block => block.type === 'image').length, 1);
});

test('unsaved title and text are protected on close, refresh and navigation until explicitly discarded', async t => {
  const app = await page(t, { body: '原内容' });
  const dialog = app.w.document.getElementById('codex-tip-edit-dialog');
  const title = app.w.document.getElementById('codex-tip-title');
  dialog.showModal();
  assert.equal(await app.w.CodexTipEditor.canLeave(), true);
  title.value = '新标题'; title.dispatchEvent(new app.w.Event('input'));
  let decision = app.w.CodexTipEditor.canLeave();
  assert.equal(app.w.document.getElementById('codex-tip-leave-warning').hidden, false);
  app.w.document.getElementById('codex-tip-keep-editing').click();
  assert.equal(await decision, false);
  assert.equal(dialog.open, true);
  const unload = new app.w.Event('beforeunload', { cancelable: true });
  app.w.dispatchEvent(unload);
  assert.equal(unload.defaultPrevented, true);
  app.w.CodexTipEditor.markSaved();
  assert.equal(await app.w.CodexTipEditor.canLeave(), true);
  app.state.editor.commands.insertContent('修改');
  const closing = app.w.CodexTipEditor.close();
  app.w.document.getElementById('codex-tip-discard').click();
  await closing;
  assert.equal(dialog.open, false);
});

test('uploads prevent leaving and keyboard save submits the form instead of saving the browser page', async t => {
  const app = await page(t);
  const dialog = app.w.document.getElementById('codex-tip-edit-dialog');
  dialog.showModal();
  let finish;
  app.state.fetch = () => new Promise(resolve => { finish = resolve; });
  app.paste();
  assert.equal(await app.w.CodexTipEditor.canLeave(), false);
  assert.equal(app.w.document.querySelector('[data-tip-mode="preview"]').disabled, true);
  finish({ ok: true, json: async () => ({ success: 1, file: asset }) });
  await app.uploaded();
  app.w.document.getElementById('codex-tip-title').value = '测试';
  let submits = 0;
  app.w.document.getElementById('codex-tip-form').addEventListener('submit', event => { event.preventDefault(); submits++; });
  const event = new app.w.KeyboardEvent('keydown', { key: 's', metaKey: true, bubbles: true, cancelable: true });
  dialog.dispatchEvent(event);
  assert.equal(event.defaultPrevented, true);
  assert.equal(submits, 1);
});

test('the file picker inserts an image and Enter exits its caption without duplicating the image', async t => {
  const app = await page(t, { body: '步骤' });
  const picker = app.w.document.getElementById('codex-tip-image-file');
  Object.defineProperty(picker, 'files', { value: [new app.w.File(['fixture'], 'upload.png', { type: 'image/png' })] });
  picker.dispatchEvent(new app.w.Event('change'));
  await app.uploaded();
  let captionPosition;
  app.state.editor.state.doc.descendants((node, pos) => { if (node.type.name === 'tipCaption') captionPosition = pos + 1; });
  app.state.editor.commands.setTextSelection(captionPosition);
  app.state.editor.commands.insertContent('图片说明');
  assert.ok(app.w.document.querySelector('#codex-tip-body img'));
  assert.equal(app.button('codeBlock').disabled, true);
  assert.equal(app.state.editor.commands.toggleCodeBlock(), false);
  app.state.editor.view.dom.dispatchEvent(new app.w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  app.state.editor.commands.insertContent('图后文字');
  const blocks = await app.blocks();
  assert.equal(blocks.filter(block => block.type === 'image').length, 1);
  assert.equal(blocks.find(block => block.type === 'image').data.caption, '图片说明');
  assert.equal(blocks.at(-1).data.text, '图后文字');
});

test('reading images still opens the real PhotoSwipe gallery', async t => {
  const app = await page(t, { blocks: [{ type: 'image', data: { file: asset, caption: '图注' } }] });
  app.w.eval(fs.readFileSync(path.join(root, 'vendor/photoswipe/photoswipe.js'), 'utf8'));
  const dialog = app.w.document.createElement('dialog');
  const article = app.w.document.createElement('div');
  dialog.append(article); app.w.document.body.append(dialog);
  app.w.CodexTipContent.render({ blocks: await app.blocks() }, article);
  app.w.CodexTipContent.imageGallery(article, dialog);
  article.querySelector('img').click();
  assert.ok(dialog.querySelector('.pswp'));
  await new Promise(resolve => app.w.requestAnimationFrame(() => app.w.requestAnimationFrame(resolve)));
  dialog.dispatchEvent(new app.w.Event('close'));
  for (let i = 0; i < 30 && dialog.querySelector('.pswp'); i++) await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(dialog.querySelector('.pswp'), null);
});
