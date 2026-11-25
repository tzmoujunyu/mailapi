// The shell lives for the session; each existing page controller mounts once.
export function createWorkspaceRouter({loadController = name => import(`/api/test-ui/views/${name}.js`)} = {}) {
  const initialRoot = document.querySelector('[data-test-view]');
  if (!initialRoot) return null;
  const routes = {'/': 'home', '/preview': 'home', '/admin': 'admin', '/admin/access': 'access', '/test-features': 'features'};
  function routeFor(url) {
    const page = routes[url.pathname];
    if (url.origin !== location.origin || !page) return null;
    if (url.pathname === '/preview') {
      if ([...url.searchParams.keys()].some(key => key !== 'user_id') || url.searchParams.getAll('user_id').length > 1) return null;
      return {page, key: 'preview:' + (url.searchParams.get('user_id') || '')};
    }
    return url.search ? null : {page, key: page};
  }
  const shellSearch = document.querySelector('.test-ui-header-search');
  const sharedStyle = document.querySelector('link[href="/static/test-ui.css"]');
  const views = new Map();
  const accountId = initialRoot.dataset.testAccountId;
  const accountRole = initialRoot.dataset.testAccountRole;
  history.scrollRestoration = 'manual';
  let current, sequence = 0, pending;
  const status = document.createElement('p');
  status.className = 'test-navigation-status';
  status.setAttribute('role', 'status');
  status.hidden = true;
  document.body.append(status);
  const translate = text => window.SiteLanguage?.t(text) || text;
  const financeEvents = new Set(['finance:changed', 'finance:notifications-changed', 'finance:open-application']);

  function createView(root, page, styles) {
    const view = {root, page, styles, search: null, active: false, mounted: false, scroll: 0};
    const documentEvents = [], windowEvents = [], intervals = new Set();
    const subscriptions = new AbortController();
    const localWindow = Object.create(null);
    localWindow.__USER_PREVIEW__ = root.hasAttribute('data-test-preview-user');
    localWindow.__PREVIEW_USER_ID__ = root.dataset.testPreviewUser || '';
    function findAll(selector) {
      return [...root.querySelectorAll(selector), ...(view.search ? view.search.matches(selector)
        ? [view.search, ...view.search.querySelectorAll(selector)] : [...view.search.querySelectorAll(selector)] : [])];
    }
    const scopedDocument = new Proxy(document, {get(target, key) {
      if (key === 'getElementById') return id => {
        const selector = '#' + CSS.escape(id);
        return root.querySelector(selector) || (view.search?.id === id ? view.search : view.search?.querySelector(selector)) || null;
      };
      if (key === 'querySelector') return selector => root.querySelector(selector)
        || (view.search?.matches(selector) ? view.search : view.search?.querySelector(selector)) || null;
      if (key === 'querySelectorAll') return findAll;
      if (key === 'body') return root;
      if (key === 'hidden') return !view.active || document.hidden;
      if (key === 'addEventListener') return (type, listener, options) => {
        if (['visibilitychange', 'scroll', 'site:language'].includes(type) || financeEvents.has(type)) {
          documentEvents.push([type, listener]);
          target.addEventListener(type, event => { if (view.active) listener(event); }, {...(typeof options === 'boolean' ? {capture: options} : options), signal: subscriptions.signal});
        } else root.addEventListener(type, listener, options);
      };
      if (key === 'dispatchEvent') return event => (financeEvents.has(event.type) ? target : root).dispatchEvent(event);
      const value = Reflect.get(target, key, target);
      return typeof value === 'function' ? value.bind(target) : value;
    }});
    const scopedWindow = new Proxy(window, {get(target, key) {
      if (key in localWindow) return localWindow[key];
      if (key === 'document') return scopedDocument;
      if (key === 'addEventListener') return (type, listener, options) => {
        windowEvents.push([type, listener]);
        target.addEventListener(type, event => { if (view.active) listener(event); }, {...(typeof options === 'boolean' ? {capture: options} : options), signal: subscriptions.signal});
      };
      if (key === 'scrollTo') return (...args) => { if (view.active && root.isConnected) target.scrollTo(...args); };
      const value = Reflect.get(target, key, target);
      return ['matchMedia', 'getComputedStyle'].includes(key) ? value.bind(target) : value;
    }, set(target, key, value) { localWindow[key] = value; return true; }});
    view.context = {
      document: scopedDocument, window: scopedWindow,
      ResizeObserver: class extends window.ResizeObserver {
        constructor(callback) { super((entries, observer) => { if (view.active && root.isConnected) callback(entries, observer); }); }
      },
      setInterval(callback, delay, ...args) {
        const id = window.setInterval(() => { if (view.active && !document.hidden) callback(...args); }, delay);
        intervals.add(id);
        return id;
      },
      clearInterval(id) { intervals.delete(id); window.clearInterval(id); },
    };
    view.resume = (hashChanged, returned) => {
      for (const [type, listener] of windowEvents) {
        if (type === 'hashchange') listener(new HashChangeEvent('hashchange'));
        else if (type === 'focus' && !hashChanged && returned) listener(new Event('focus'));
      }
      if (!hashChanged && returned) for (const [type, listener] of documentEvents) {
        if (type === 'visibilitychange') listener(new Event(type));
      }
      if (!hashChanged && returned) root.dispatchEvent(new CustomEvent('test:view-enter'));
    };
    view.dispose = () => { view.active = false; subscriptions.abort(); for (const id of intervals) window.clearInterval(id); };
    return view;
  }

  async function mount(view) {
    const controller = view.controller || await loadController(view.page);
    controller.mount(view.context);
    // The editor is the only lazy, page-bound script. Vendor libraries keep their
    // normal loader, while the editor receives the console's own DOM scope.
    const performance = view.context.window.PagePerformance;
    if (performance) {
      const loadScript = performance.loadScript;
      let editor;
      performance.loadScript = source => source === '/static/codex-tip-editor.js'
        ? (editor ||= loadController('editor').then(module => module.mount(view.context)))
        : loadScript(source);
    }
    view.mounted = true;
  }

  function moveSearch(view) {
    view.search ||= view.root.querySelector('.workspace-search, .management-search');
    if (view.search) shellSearch.append(view.search);
  }

  function enableStyles(view, enabled) {
    view.styles.forEach(style => { style.media = enabled ? (style.dataset.originalMedia || '') : 'not all'; });
  }

  async function loadStyles(styles) {
    await Promise.all(styles.map(style => new Promise((resolve, reject) => {
      style.dataset.originalMedia = style.media || '';
      style.media = 'not all';
      if (style.tagName === 'LINK') {
        style.onload = resolve;
        style.onerror = () => reject(new Error('页面加载失败，请重试'));
      }
      document.head.insertBefore(style, sharedStyle);
      if (style.tagName !== 'LINK') resolve();
    })));
  }

  function activate(view, url, {push = true, restore = false} = {}) {
    const previous = current;
    const sameView = previous === view;
    if (!sameView && previous) {
      previous.scroll = window.scrollY;
      previous.active = false;
      // Cached pages retain their form/list state, but never leave a modal in
      // the top layer while another page is visible.
      previous.root.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
      if (previous.search) previous.root.append(previous.search);
      previous.root.remove();
      enableStyles(previous, false);
    }
    if (!sameView) {
      enableStyles(view, true);
      document.body.classList.remove('home-page', 'test-ui-home', 'test-ui-features', 'test-ui-access', 'test-ui-admin');
      document.body.classList.add('test-ui-' + view.page);
      if (view.page === 'home') document.body.classList.add('home-page');
      document.body.append(view.root);
      moveSearch(view);
    }
    current = view;
    view.active = true;
    document.title = view.title;
    if (push && url.href !== location.href) history.pushState(null, '', url);
    view.url = url.href;
    if (view.mounted) view.resume(sameView, view.entered);
    view.entered = true;
    window.scrollTo({top: restore ? view.scroll : 0, behavior: 'instant'});
    const anchor = url.hash && !url.hash.startsWith('#view-')
      ? view.root.querySelector(`[id="${CSS.escape(decodeURIComponent(url.hash.slice(1)))}"]`) : null;
    if (anchor) anchor.scrollIntoView({block: 'start'});
    window.dispatchEvent(new CustomEvent('test:navigation'));
  }

  async function navigate(input, {push = true, restore = false} = {}) {
    const url = new URL(input, location.href);
    const route = routeFor(url);
    if (!route) return false;
    if(url.href!==current.url && current.context.window.FinanceDrafts && !await current.context.window.FinanceDrafts.canLeave()) {
      if(!push)history.replaceState(null,'',current.url);
      return true;
    }
    const {page, key} = route;
    const token = ++sequence;
    pending?.abort();
    pending = new AbortController();
    status.hidden = true;
    current.root.setAttribute('aria-busy', 'true');
    const progress = setTimeout(() => {
      if (token === sequence) { status.textContent = translate('正在加载…'); status.hidden = false; }
    }, 150);
    try {
      let view = views.get(key);
      const session = await fetch('/api/auth/me', {cache: 'no-store', signal: pending.signal});
      if (token !== sequence) return true;
      if (session.status === 401) { location.assign('/login'); return true; }
      if (!session.ok) throw new Error('页面加载失败，请重试');
      const identity = await session.json();
      if ((accountId && identity.user.id !== accountId) || (accountRole && identity.user.role !== accountRole)) {
        // A login in another tab changed the account. Do not reuse private views.
        location.assign(url.href);
        return true;
      }
      if (((page === 'admin' || url.pathname === '/preview') && identity.user.role !== 'admin') ||
          (page === 'access' && !['admin', 'manager'].includes(identity.user.role))) {
        throw new Error('页面加载失败，请重试');
      }
      window.dispatchEvent(new CustomEvent('test:session', {detail: identity}));
      if (current !== view) {
        if (!view) {
          const response = await fetch(url.pathname + url.search, {cache: 'no-store', signal: pending.signal});
          if (token !== sequence) return true;
          if (new URL(response.url || url, location.href).pathname === '/login') {
            location.assign('/login');
            return true;
          }
          if (!response.ok || (response.url && new URL(response.url).pathname !== url.pathname)) {
            throw new Error('页面加载失败，请重试');
          }
          const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
          const root = parsed.querySelector(`[data-test-view="${page}"]`);
          if (!root) throw new Error('页面加载失败，请重试');
          if (url.pathname === '/preview' && url.searchParams.get('user_id')
              && root.dataset.testPreviewUser !== url.searchParams.get('user_id')) throw new Error('页面加载失败，请重试');
          if (accountId && root.dataset.testAccountId !== accountId) { location.assign(url.href); return true; }
          root.querySelectorAll('script').forEach(script => script.remove());
          const styles = [...parsed.querySelectorAll('[data-test-page-style]')];
          view = createView(root, page, styles);
          view.title = parsed.title;
          // Load code before changing the content, so a failed request keeps
          // the current screen and the navigation controls usable.
          try {
            [view.controller] = await Promise.all([loadController(page), loadStyles(styles)]);
            if (token !== sequence) { styles.forEach(style => style.remove()); return true; }
            view.active = true;
            await mount(view);
            view.active = false;
          } catch (error) {
            view.dispose();
            styles.forEach(style => style.remove());
            throw error;
          }
          views.set(key, view);
        }
      }
      if (token !== sequence) return true;
      activate(view, url, {push, restore});
      window.TestUI?.enhanceResources(view.root);
      return true;
    } catch (error) {
      if (token !== sequence || error.name === 'AbortError') return true;
      status.textContent = translate('页面加载失败，请重试');
      status.hidden = false;
      if (!push) history.replaceState(null, '', current.url);
      return true;
    } finally {
      clearTimeout(progress);
      if (token === sequence) {
        current.root.removeAttribute('aria-busy');
        if (status.textContent === translate('正在加载…')) status.hidden = true;
        current.url = location.href;
      }
    }
  }

  const initial = createView(initialRoot, initialRoot.dataset.testView,
    [...document.querySelectorAll('[data-test-page-style]')]);
  initial.title = document.title;
  initial.url = location.href;
  initial.search = shellSearch.firstElementChild;
  views.set(routeFor(new URL(location.href))?.key || initial.page, initial);
  current = initial;
  initial.active = true;
  moveSearch(initial);
  const ready = mount(initial).then(() => { initial.entered = true; window.TestUI?.enhanceResources(initial.root); });
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
        || link.download || (link.target && link.target !== '_self')) return;
    const url = new URL(link.href);
    if (!routeFor(url)) return;
    event.preventDefault();
    ready.then(() => navigate(url));
  });
  window.addEventListener('popstate', () => { ready.then(() => navigate(location.href, {push: false, restore: true})); });
  const router = {navigate: (...args) => ready.then(() => navigate(...args)), ready};
  window.TestNavigation = router;
  return router;
}

createWorkspaceRouter();
