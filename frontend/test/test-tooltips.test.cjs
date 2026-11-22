const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const assets = path.resolve(__dirname, '../../mailapi_shared/static');
const read = name => fs.readFileSync(path.join(assets, name), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function setup(t, content = '') {
  const dom = new JSDOM(`<body class="test-ui-page">${content}</body>`, {url:'http://localhost', runScripts:'outside-only', pretendToBeVisual:true});
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document;
  w.HTMLElement.prototype.showPopover = function() { this.dataset.open = 'true'; };
  w.HTMLElement.prototype.hidePopover = function() { delete this.dataset.open; };
  w.eval(read('test-ui.js'));
  const fire = (node, type, props = {}) => node.dispatchEvent(new w.MouseEvent(type, {bubbles:true, ...props}));
  return {w, d, fire, tip:() => d.getElementById('test-control-tooltip')};
}

test('dynamically rendered icon actions show their names on hover without a second native hint', async t => {
  const {w,d,fire,tip} = setup(t);
  d.body.insertAdjacentHTML('beforeend', '<button title="停用" aria-label="停用 Development"><svg></svg></button>');
  const button = d.querySelector('button');
  fire(button, 'pointerover');
  assert.equal(tip(), null, 'brief pointer crossings do not flash a tooltip');
  await new Promise(resolve => w.setTimeout(resolve, 280));
  assert.equal(tip().textContent, '停用');
  assert.equal(tip().hidden, false);
  assert.equal(button.hasAttribute('title'), false);
  fire(button, 'pointerout', {relatedTarget:tip()});
  fire(tip(), 'pointerover', {relatedTarget:button});
  await new Promise(resolve => w.setTimeout(resolve, 120));
  assert.equal(tip().hidden, false, 'the pointer can move into the hint');
  fire(tip(), 'pointerout', {relatedTarget:d.body});
  await new Promise(resolve => w.setTimeout(resolve, 120));
  assert.equal(tip().hidden, true);
  assert.equal(button.title, '停用');
});

test('keyboard hints preserve existing descriptions, stay inside modals and dismiss without swallowing Escape', t => {
  const {w,d,tip} = setup(t, '<p id="help">Existing help</p><dialog open><button aria-label="关闭" aria-describedby="help">×</button></dialog>');
  const button = d.querySelector('button');
  button.focus();
  assert.equal(tip().textContent, '关闭');
  assert.equal(tip().closest('dialog'), button.closest('dialog'));
  assert.equal(button.getAttribute('aria-describedby'), 'help test-control-tooltip');
  button.dispatchEvent(new w.Event('scroll', {bubbles:true}));
  assert.equal(tip().hidden, false, 'scrolling a keyboard-focused table action into view keeps its hint visible');
  const escape = new w.KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true});
  button.dispatchEvent(escape);
  assert.equal(escape.defaultPrevented, false);
  assert.equal(tip().hidden, true);
  assert.equal(button.getAttribute('aria-describedby'), 'help');
});

test('hints are safe text, clamp to viewport edges and flip above bottom controls', t => {
  const {d,tip} = setup(t, '<button aria-label="&lt;img src=x onerror=alert(1)&gt;"><svg></svg></button>');
  const button = d.querySelector('button');
  button.getBoundingClientRect = () => ({left:1000, right:1030, top:750, bottom:780, width:30, height:30});
  button.focus();
  Object.defineProperties(tip(), {offsetWidth:{value:200}, offsetHeight:{value:32}});
  button.blur(); button.focus();
  assert.equal(tip().querySelector('img'), null);
  assert.equal(tip().textContent, '<img src=x onerror=alert(1)>');
  assert.equal(tip().style.left, '816px');
  assert.equal(tip().style.top, '710px');
});

test('activation, navigation, language changes and replaced controls never leave stale hints', async t => {
  const {w,d,tip} = setup(t, '<button title="停用" aria-label="停用 key"><svg></svg></button>');
  const button = d.querySelector('button');
  for (const name of ['test:navigation', 'resize', 'hashchange']) {
    button.blur(); button.focus();
    w.dispatchEvent(new w.Event(name));
    assert.equal(tip().hidden, true);
  }
  button.blur(); button.focus();
  button.click();
  assert.equal(tip().hidden, true);
  button.blur(); button.focus();
  button.setAttribute('aria-label', '启用 key'); button.title = '启用';
  await tick();
  assert.equal(tip().hidden, true);
  assert.equal(button.title, '启用', 'a changed action is never restored to its old name');
  button.blur(); button.focus();
  d.dispatchEvent(new w.Event('site:language'));
  assert.equal(tip().hidden, true);
  button.blur(); button.focus();
  button.remove(); await tick();
  assert.equal(tip().hidden, true);
});

test('visible text buttons, rich help and the existing theme tooltip remain unduplicated', t => {
  const {d,tip} = setup(t, '<button aria-label="申请报销">申请报销</button><button class="help-hint-trigger" aria-label="帮助">?</button><button class="site-theme-button" aria-label="主题"><svg></svg></button><button aria-label="第 2 页">2</button>');
  for (const button of d.querySelectorAll('button')) { button.focus(); assert.equal(tip(), null); }
});

test('avatar hints describe the account menu rather than repeating only a username', t => {
  const {d,tip} = setup(t, '<button class="test-ui-avatar-button" aria-label="账户菜单：admin" title="admin"><span aria-hidden="true">AD</span></button>');
  d.querySelector('button').focus();
  assert.equal(tip().textContent, '账户菜单：admin');
});

test('collapsed navigation labels get hints and expanded labels do not duplicate visible text', t => {
  const {d,tip} = setup(t, '<nav class="test-ui-nav"><a href="/admin/access" aria-label="账户"><svg></svg><span style="opacity:0;max-width:0">账户</span></a></nav>');
  const link = d.querySelector('a');
  link.focus();
  assert.equal(tip().textContent, '账户');
  link.blur(); link.querySelector('span').style.opacity = '1'; link.focus();
  assert.equal(tip().hidden, true);
});

test('opening a menu dismisses the trigger hint instead of covering its menu items', async t => {
  const {d,tip} = setup(t, '<button title="更多操作" aria-haspopup="menu" aria-expanded="false"><svg></svg></button>');
  const button=d.querySelector('button');button.focus();assert.equal(tip().hidden,false);
  button.setAttribute('aria-expanded','true');await tick();assert.equal(tip().hidden,true);
  button.blur();button.focus();assert.equal(tip().hidden,true);
});

test('dynamic group action names follow English and Chinese without translating user group names', async t => {
  const {w,d,tip} = setup(t, '<button title="删除 研究组" aria-label="删除分组 · 研究组"><svg></svg></button>');
  w.eval(read('test-i18n-en.js')); w.eval(read('test-i18n.js'));
  const button = d.querySelector('button');
  await w.SiteLanguage.set('en');
  button.focus();
  assert.equal(tip().textContent, 'Delete 研究组');
  assert.equal(button.getAttribute('aria-label'), 'Delete group · 研究组');
  await w.SiteLanguage.set('zh-CN');
  await new Promise(resolve => w.requestAnimationFrame(resolve));
  assert.equal(tip().hidden, true);
  assert.equal(button.title, '删除 研究组');
  button.blur(); button.focus();
  assert.equal(tip().textContent, '删除 研究组');
});

test('test-only icon controls in every page template have explicit accessible names', t => {
  for (const name of ['index.html','access.html','admin.html','login.html','test-features.html']) {
    const dom = new JSDOM(read(name));
    t.after(() => dom.window.close());
    for (const control of dom.window.document.querySelectorAll('button,a[href],summary')) {
      if (control.matches('[aria-hidden="true"]')) continue;
      const copy = control.cloneNode(true);
      copy.querySelectorAll('svg,[aria-hidden="true"],[hidden]').forEach(node => node.remove());
      const text = copy.textContent.trim();
      if (/[\p{L}\p{N}]/u.test(text)) continue;
      assert.ok(control.getAttribute('aria-label') || control.getAttribute('aria-labelledby') || control.getAttribute('title'), `${name}: ${control.outerHTML}`);
    }
  }
});
