const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const root = process.env.MAILAPI_UI_ROOT || 'static';
const monitorSource = fs.readFileSync(`${root}/performance-monitor.js`, 'utf8');

function page(enabled = true) {
  const observers = [];
  const listeners = {};
  const logs = [];
  const document = {
    documentElement: { dataset: { performanceMonitor: enabled ? 'test' : 'disabled' } },
    baseURI: 'https://example.test/',
    visibilityState: 'visible',
    addEventListener(type, listener) { (listeners[type] ||= []).push(listener); },
  };
  const context = vm.createContext({
    URL,
    document,
    console: { info(...args) { logs.push(args); } },
    performance: {
      now: () => 100,
      getEntriesByType: () => [{ responseStart: 321.45 }],
    },
    window: {
      location: { href: 'https://example.test/' },
      addEventListener(type, listener) { (listeners[type] ||= []).push(listener); },
      PerformanceObserver: class {
        constructor(callback) { this.callback = callback; observers.push(this); }
        observe(options) { this.options = options; }
      },
      fetch: async () => ({ status: 204 }),
    },
  });
  vm.runInContext(monitorSource, context);
  return { context, document, observers, listeners, logs };
}

test('production pages do not activate the monitor', () => {
  const app = page(false);
  assert.equal(app.context.window.__MAILAPI_PERF__, undefined);
});

test('test pages record web vitals and fetch timings without query strings', async () => {
  const app = page();
  const monitor = app.context.window.__MAILAPI_PERF__;
  assert.ok(monitor);
  const entries = {
    'largest-contentful-paint': [{ startTime: 850.2 }],
    'layout-shift': [{ value: 0.04, hadRecentInput: false }, { value: 0.3, hadRecentInput: true }],
    event: [{ interactionId: 9, duration: 65.4 }],
  };
  for (const observer of app.observers) observer.callback({ getEntries: () => entries[observer.options.type] || [] });
  await app.context.window.fetch('/api/accounts?secret=redacted');
  const snapshot = monitor.snapshot();
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.metrics)), { lcp: 850.2, inp: 65.4, cls: 0.04, ttfb: 321.45 });
  assert.equal(snapshot.requests[0].path, '/api/accounts');
  assert.equal(snapshot.requests[0].average, 0);
  assert.equal(snapshot.recentRequests[0].duration, 0);
  assert.equal(snapshot.recentRequests[0].status, 204);
});

test('both rendered pages provide a test-only monitor placeholder', () => {
  for (const file of ['index.html', 'admin.html']) {
    const source = fs.readFileSync(`${root}/${file}`, 'utf8');
    assert.match(source, /data-performance-monitor="disabled"/);
    assert.match(source, /<!-- PERFORMANCE_MONITOR_SCRIPT -->/);
  }
});
