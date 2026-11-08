const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

async function page() {
  const nodes = new Map();
  const users = [{ id: 'admin', username: 'admin', role: 'admin' }, { id: 'alice', username: 'alice', role: 'user' }];
  const groups = [
    { id: 'agent-1', name: 'Agent-1', member_ids: ['alice'], resource_keys: ['gpt:first', 'gpt:second', 'coding-plan:one'] },
    { id: 'agent-2', name: 'Agent-2', member_ids: [], resource_keys: ['gpt:first', 'relay-account:one'] },
    { id: 'vla-1', name: 'VLA-1', member_ids: [], resource_keys: [] },
  ];
  const resources = [
    { key: 'gpt:first', kind: 'gpt', name: 'complete.account.name@example.com' },
    { key: 'gpt:second', kind: 'gpt', name: 'second@example.com', listed: false },
    { key: 'gpt:unused', kind: 'gpt', name: '<img src=x onerror=alert(1)>' },
    { key: 'coding-plan:one', kind: 'coding-plan', name: 'GLM Demo' },
    { key: 'relay-account:one', kind: 'relay-account', name: 'Relay Demo' },
  ];
  const element = () => ({ innerHTML: '', textContent: '', value: '', listeners: {},
    classList: { toggle() {} }, reset() {}, focus() {}, showModal() {},
    addEventListener(type, callback) { this.listeners[type] = callback; },
  });
  const document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
    querySelectorAll() { return []; }, querySelector() { return null; },
  };
  const responses = { '/api/auth/me': { user: users[0] }, '/api/admin/users': { items: users },
    '/api/admin/groups': { items: groups }, '/api/admin/resources': { items: resources } };
  const context = vm.createContext({ document, console, window: { addEventListener() {} },
    fetch: async url => ({ ok: true, json: async () => responses[url] }), confirm: () => true });
  const root = process.env.MAILAPI_UI_ROOT || 'static';
  vm.runInContext(fs.readFileSync(path.join(root, 'access.js'), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  return { nodes, context };
}

test('overview shows a complete first GPT account and indicates additional accounts', async () => {
  const app = await page();
  const html = app.nodes.get('group-overview').innerHTML;
  const summaries = [...html.matchAll(/<span class="matrix-account">([\s\S]*?)<\/span>\s*<\/button>/g)].map(match => match[1]);
  assert.equal(summaries.length, 3);
  assert.match(summaries[0], /complete\.account\.name@example\.com.*等多个账号/);
  assert.match(summaries[1], /complete\.account\.name@example\.com/);
  assert.doesNotMatch(summaries[1], /等多个账号/);
  assert.match(summaries[2], /暂无 GPT 账号/);
  assert.match(html, /data-search-text="[^"]*second@example\.com/);
});

test('group resource sections retain selections and count saved group assignments', async () => {
  const app = await page();
  vm.runInContext('openGroup(groups[0])', app.context);
  const html = app.nodes.get('resource-options').innerHTML;
  assert.ok(html.indexOf('data-resource-kind="gpt"') < html.indexOf('data-resource-kind="coding-plan"'));
  assert.ok(html.indexOf('data-resource-kind="coding-plan"') < html.indexOf('data-resource-kind="relay-account"'));
  const labels = [...html.matchAll(/<label class="option">([\s\S]*?)<\/label>/g)].map(match => match[1]);
  assert.match(labels.find(label => label.includes('value="gpt:first"')), /checked[\s\S]*2 Group shared/);
  assert.match(labels.find(label => label.includes('value="gpt:second"')), /checked[\s\S]*已下架[\s\S]*1 Group shared/);
  assert.match(labels.find(label => label.includes('value="gpt:unused"')), /0 Group shared/);
  assert.doesNotMatch(labels.find(label => label.includes('value="coding-plan:one"')), /Group shared/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<img/);
  vm.runInContext('groups[1].resource_keys = []; openGroup(groups[0])', app.context);
  assert.match(app.nodes.get('resource-options').innerHTML, /value="gpt:first"[\s\S]*?1 Group shared/);
  vm.runInContext('openGroup(groups[0], {membersOnly:true})', app.context);
  assert.equal(app.nodes.get('group-resources').hidden, true);
});

test('overview escapes account names and search metadata', async () => {
  const app = await page();
  vm.runInContext('resources[0].name = "<img src=x onerror=alert(1)>"; render()', app.context);
  const html = app.nodes.get('group-overview').innerHTML;
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<img/);
});
