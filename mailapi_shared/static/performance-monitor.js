(() => {
  const root = document.documentElement;
  if (root?.dataset.performanceMonitor !== 'test') return;

  const startedAt = typeof performance?.now === 'function' ? performance.now() : 0;
  const metrics = { lcp: null, inp: null, cls: 0, ttfb: null };
  const recentRequests = [];
  const requestGroups = new Map();

  function now() {
    return typeof performance?.now === 'function' ? performance.now() : Date.now();
  }

  function round(value) {
    return value == null ? null : Math.round(value * 100) / 100;
  }

  function requestPath(input) {
    try {
      const raw = typeof input === 'string' ? input : input?.url;
      if (!raw) return '';
      return new URL(raw, window.location?.href || document.baseURI).pathname;
    } catch {
      return '';
    }
  }

  function recordRequest(input, method, status, duration, error) {
    const path = requestPath(input) || '[unknown]';
    const normalizedMethod = String(method || 'GET').toUpperCase();
    const key = `${normalizedMethod} ${path}`;
    const item = {
      method: normalizedMethod,
      path,
      status: status == null ? null : Number(status),
      duration: round(duration),
      error: Boolean(error),
    };
    recentRequests.push(item);
    if (recentRequests.length > 100) recentRequests.shift();
    const summary = requestGroups.get(key) || {
      method: normalizedMethod, path, count: 0, errors: 0, total: 0, max: 0,
    };
    summary.count += 1;
    if (item.error || (item.status != null && item.status >= 400)) summary.errors += 1;
    summary.total = round(summary.total + item.duration);
    summary.max = Math.max(summary.max, item.duration || 0);
    requestGroups.set(key, summary);
  }

  function readNavigationTiming() {
    if (typeof performance?.getEntriesByType !== 'function') return;
    const navigation = performance.getEntriesByType('navigation')[0];
    if (navigation && Number.isFinite(navigation.responseStart)) {
      metrics.ttfb = round(navigation.responseStart);
    }
  }

  function observe(type, handler, options = {}) {
    const PerformanceObserverClass = window.PerformanceObserver;
    if (!PerformanceObserverClass) return;
    try {
      const observer = new PerformanceObserverClass(list => handler(list.getEntries()));
      observer.observe({ type, buffered: true, ...options });
    } catch {
      // Browsers without this entry type simply leave the metric unavailable.
    }
  }

  observe('largest-contentful-paint', entries => {
    const entry = entries.at(-1);
    if (entry) metrics.lcp = round(entry.renderTime || entry.loadTime || entry.startTime);
  });
  observe('layout-shift', entries => {
    for (const entry of entries) {
      if (!entry.hadRecentInput) metrics.cls = round(metrics.cls + Number(entry.value || 0));
    }
  });
  observe('event', entries => {
    for (const entry of entries) {
      if (entry.interactionId && Number.isFinite(entry.duration)) {
        metrics.inp = Math.max(metrics.inp || 0, round(entry.duration));
      }
    }
  }, { durationThreshold: 40 });
  readNavigationTiming();

  const originalFetch = window.fetch;
  if (typeof originalFetch === 'function') {
    window.fetch = async function monitoredFetch(input, init) {
      const requestInit = init || {};
      const method = requestInit.method || input?.method || 'GET';
      const started = now();
      try {
        const response = await originalFetch.apply(this, arguments);
        recordRequest(input, method, response.status, now() - started, false);
        return response;
      } catch (error) {
        recordRequest(input, method, null, now() - started, true);
        throw error;
      }
    };
  }

  const api = {
    metrics,
    snapshot() {
      readNavigationTiming();
      return {
        metrics: { ...metrics },
        requests: [...requestGroups.values()].map(item => ({
          ...item,
          average: item.count ? round(item.total / item.count) : 0,
        })),
        recentRequests: recentRequests.slice(),
      };
    },
  };
  window.__MAILAPI_PERF__ = api;

  function report() {
    readNavigationTiming();
    if (window.__MAILAPI_PERFORMANCE_LOG__ !== false) {
      console.info('[MailAPI performance]', api.snapshot());
    }
  }
  window.addEventListener('load', report, { once: true });
  window.addEventListener('pagehide', report, { once: true });
  window.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') report();
  });
})();
