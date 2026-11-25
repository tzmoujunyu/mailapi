const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '../../mailapi_shared/static');
const asset = scope => ({ url: `/api/content-images/${scope}/${'b'.repeat(32)}.webp`,
  preview: `/api/content-images/${scope}/${'b'.repeat(32)}.preview.webp`, width: 800, height: 400 });

async function page(t) {
  const dom = new JSDOM('', { url: 'https://example.test', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window, instances = [];
  w.matchMedia = () => ({ matches: true });
  w.scrollTo = () => {};
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event('close')); };
  w.Range.prototype.getClientRects = () => [];
  w.Range.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 });
  for (const script of ['vendor/tiptap/tiptap.js', 'codex-tip-content.js', 'codex-tip-format.js', 'shared-content-editor.js', 'shared-content-dialog.js']) {
    w.eval(fs.readFileSync(path.join(root, script), 'utf8'));
  }
  const RealEditor = w.MailapiTiptap.Editor;
  let tiptap;
  w.MailapiTiptap = { ...w.MailapiTiptap, Editor: class extends RealEditor { constructor(options) { super(options); tiptap = this; } } };
  const requests = [];
  let uploadAsset = asset('finance');
  w.fetch = async (url, request) => { requests.push({ url, request }); return { ok: true, json: async () => ({ success: 1, file: uploadAsset }) }; };
  async function create(scope, content = { body: '<old>\n第二行' }) {
    const prefix = `${scope}-notice`;
    const dialog = w.SharedContentDialog.create({ prefix });
    w.document.body.append(dialog);
    const find = suffix => dialog.querySelector(`[id="${prefix}-${suffix}"]`);
    const pinned = w.document.createElement('input'); pinned.type = 'checkbox';
    find('form').append(pinned);
    const editor = w.SharedContentEditor.create({ dialog, prefix, uploadURL: `/api/announcement-images/${scope}`,
      imagePrefix: `/api/content-images/${scope}/`, extraFingerprint: () => pinned.checked });
    instances.push(editor);
    await editor.open(content); dialog.showModal();
    return { editor, dialog, find, pinned, tiptap, blocks: async () => JSON.parse(JSON.stringify(await editor.save())) };
  }
  t.after(() => { instances.forEach(editor => editor.destroy()); w.close(); });
  return { w, create, requests, useAsset: value => { uploadAsset = value; } };
}

test('website and finance dialogs isolate text, toolbar selections and preview', async t => {
  const app = await page(t), site = await app.create('site'), finance = await app.create('finance', { body: '财务说明' });
  assert.equal(new Set([...app.w.document.querySelectorAll('[id]')].map(node => node.id)).size, app.w.document.querySelectorAll('[id]').length);
  site.tiptap.commands.setTextSelection({ from: 1, to: 6 });
  site.dialog.querySelector('[data-tip-action="bold"]').click();
  assert.match((await site.blocks())[0].data.text, /<b>&lt;old&gt;<\/b>/);
  assert.equal((await finance.blocks())[0].data.text, '财务说明');
  site.dialog.querySelector('[data-tip-mode="preview"]').click();
  assert.ok(site.find('preview').querySelector('b'));
  assert.equal(finance.find('preview').hidden, true);
  assert.equal(site.find('body').querySelector('[role="textbox"]').getAttribute('aria-labelledby'), 'site-notice-body-label');
});

test('announcement uploads use their scope and reject another scope without losing text', async t => {
  const app = await page(t), finance = await app.create('finance');
  async function paste() {
    const event = new app.w.Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'clipboardData', { value: { files: [new app.w.File(['image'], 'invoice.png', { type: 'image/png' })], getData: () => '' } });
    finance.tiptap.view.dom.dispatchEvent(event);
    for (let i = 0; i < 100 && finance.editor.busy(); i++) await new Promise(resolve => setImmediate(resolve));
  }
  await paste();
  assert.equal(app.requests[0].url, '/api/announcement-images/finance');
  assert.deepEqual((await finance.blocks()).find(block => block.type === 'image').data.file, asset('finance'));
  const before = await finance.blocks();
  app.useAsset(asset('site')); await paste();
  assert.deepEqual(await finance.blocks(), before);
  assert.match(finance.find('error').textContent, /图片上传失败/);
  await assert.rejects(finance.editor.open({ blocks: [{ type: 'image', data: { file: asset('site'), caption: '' } }] }), /其他内容范围/);
});

test('pin-only changes require an explicit discard and saved changes stop refresh warnings', async t => {
  const app = await page(t), site = await app.create('site');
  assert.equal(await site.editor.canLeave(), true);
  site.pinned.checked = true;
  site.pinned.dispatchEvent(new app.w.Event('change', { bubbles: true }));
  assert.match(site.find('save-state').textContent, /未保存/);
  const pending = site.editor.canLeave();
  assert.equal(site.find('leave-warning').hidden, false);
  site.find('keep-editing').click(); assert.equal(await pending, false);
  const before = new app.w.Event('beforeunload', { cancelable: true }); app.w.dispatchEvent(before);
  assert.equal(before.defaultPrevented, true);
  site.editor.markSaved();
  assert.equal(await site.editor.canLeave(), true);
  const after = new app.w.Event('beforeunload', { cancelable: true }); app.w.dispatchEvent(after);
  assert.equal(after.defaultPrevented, false);
});

test('the shared editor enforces 8000 visible characters and keeps old literal text', async t => {
  const app = await page(t), finance = await app.create('finance');
  assert.match((await finance.blocks())[0].data.text, /&lt;old&gt;<br>/);
  finance.tiptap.commands.setContent('<p>' + '字'.repeat(8000) + '</p>');
  assert.equal((await finance.blocks())[0].data.text.length, 8000);
  finance.tiptap.commands.insertContent('多');
  await assert.rejects(finance.editor.save(), /8000/);
});

test('reading only accepts controlled content image paths and strips unsafe markup', async t => {
  const app = await page(t), target = app.w.document.createElement('div');
  app.w.CodexTipContent.render({ blocks: [
    { type: 'paragraph', data: { text: '<a href="javascript:alert(1)">bad</a><img src=x onerror=alert(1)>' } },
    ...['site', 'finance'].map(scope => ({ type: 'image', data: { file: asset(scope) } })),
    { type: 'image', data: { file: { url: '/api/content-images/other/' + 'b'.repeat(32) + '.webp' } } },
  ] }, target);
  assert.equal(target.querySelectorAll('img').length, 2);
  assert.equal(target.querySelectorAll('[onerror], a[href^="javascript:"]').length, 0);
});
