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
    { key: 'mailbox:one', kind: 'mailbox', name: 'mail@example.com', listed: false },
    { key: 'coding-plan:one', kind: 'coding-plan', name: 'GLM Demo' },
    { key: 'relay-account:one', kind: 'relay-account', name: 'Relay Demo' },
  ];
  const element = () => ({ html: '', textContent: '', value: '', listeners: {}, inputs: [],
    set innerHTML(value) {
      this.html = value;
      this.inputs = [...value.matchAll(/<input\b([^>]*)>/g)].map(match => ({
        value: /value="([^"]*)"/.exec(match[1])?.[1] || '', checked: /\bchecked\b/.test(match[1]),
        matches: selector => selector === 'input[type="radio"]' && /type="radio"/.test(match[1]),
      }));
    },
    get innerHTML() { return this.html; },
    querySelectorAll(selector) { return selector === 'input:checked' ? this.inputs.filter(input => input.checked) : this.inputs; },
    querySelector() { return { disabled: false }; },
    classList: { toggle() {} }, reset() {}, focus() {}, showModal() { this.open = true; }, close() { this.open = false; },
    addEventListener(type, callback) { this.listeners[type] = callback; },
  });
  const document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
    querySelectorAll() { return []; }, querySelector() { return null; },
  };
  const responses = { '/api/auth/me': { user: users[0] }, '/api/admin/users': { items: users },
    '/api/admin/groups': { items: groups }, '/api/admin/resources': { items: resources } };
  const requests = [];
  const app = { nodes, requests, confirm: true, saveError: false };
  const context = vm.createContext({ document, console, window: { addEventListener() {} },
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (url === '/api/admin/resource-groups') {
        if (app.saveError) return { ok: false, json: async () => ({ detail: '保存失败' }) };
        const { resource_key, group_ids } = JSON.parse(options.body);
        const items = groups.map(group => ({ ...group, resource_keys: group_ids.includes(group.id)
          ? [...new Set([...group.resource_keys, resource_key])] : group.resource_keys.filter(key => key !== resource_key) }));
        responses['/api/admin/groups'] = { items };
        return { ok: true, json: async () => ({ items }) };
      }
      return { ok: true, json: async () => responses[url] };
    }, confirm: () => app.confirm });
  const root = process.env.MAILAPI_UI_ROOT || 'static';
  vm.runInContext(fs.readFileSync(path.join(root, 'access.js'), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  return { ...app, context, state: app };
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
  vm.runInContext('openGroup(groups[0], {fromOverview:true})', app.context);
  assert.equal(app.nodes.get('group-position').disabled, false);
  assert.equal(app.nodes.get('group-name').disabled, true);
  assert.match(app.nodes.get('resource-options').innerHTML, /value="gpt:first" checked/);
});

test('overview escapes account names and search metadata', async () => {
  const app = await page();
  vm.runInContext('resources[0].name = "<img src=x onerror=alert(1)>"; render()', app.context);
  const html = app.nodes.get('group-overview').innerHTML;
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<img/);
});

test('overview editing submits selected resources without adding details to the matrix', async () => {
  const app = await page();
  vm.runInContext('openGroup(groups[0], {fromOverview:true})', app.context);
  app.nodes.get('resource-options').inputs.forEach(input => { input.checked = input.value === 'mailbox:one'; });
  await app.nodes.get('group-form').listeners.submit({ preventDefault() {}, target: app.nodes.get('group-form') });
  const request = app.requests.find(item => item.url === '/api/admin/groups/agent-1');
  assert.deepEqual(JSON.parse(request.options.body), { name: 'Agent-1', member_ids: ['alice'], resource_keys: ['mailbox:one'] });
  assert.doesNotMatch(app.nodes.get('group-overview').innerHTML, /class="tag">[^<]*GLM Demo/);
});

function chooseResource(app, key) {
  const options = app.nodes.get('batch-resource-options');
  options.inputs.forEach(input => { input.checked = input.value === key; });
  options.listeners.change({ target: options.inputs.find(input => input.value === key) });
}

test('bulk assignment loads current groups and supports select all and clear', async () => {
  const app = await page();
  app.nodes.get('batch-resources').listeners.click();
  assert.equal(app.nodes.get('batch-resource-save').disabled, true);
  const html = app.nodes.get('batch-resource-options').innerHTML;
  for (const name of ['GPT 账号', '邮箱接码', 'Coding Plan', '中转站']) assert.ok(html.includes(name));
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  chooseResource(app, 'gpt:first');
  const right = app.nodes.get('batch-group-options');
  assert.deepEqual(right.inputs.filter(input => input.checked).map(input => input.value), ['agent-1', 'agent-2']);
  app.nodes.get('batch-groups-all').listeners.click();
  assert.equal(right.inputs.filter(input => input.checked).length, 3);
  app.nodes.get('batch-groups-none').listeners.click();
  assert.equal(right.inputs.filter(input => input.checked).length, 0);
  assert.equal(app.nodes.get('batch-group-count').textContent, '已选 0 / 3 个分组');
  chooseResource(app, 'mailbox:one');
  assert.match(app.nodes.get('batch-current-resource').textContent, /mail@example.com（已下架）/);
});

test('bulk assignment saves one resource and refreshes views while keeping the dialog open', async () => {
  const app = await page();
  app.nodes.get('batch-resources').listeners.click();
  chooseResource(app, 'gpt:first');
  const right = app.nodes.get('batch-group-options');
  right.inputs.forEach(input => { input.checked = input.value === 'vla-1'; });
  await app.nodes.get('batch-resource-form').listeners.submit({ preventDefault() {} });
  const request = app.requests.find(item => item.url === '/api/admin/resource-groups');
  assert.deepEqual(JSON.parse(request.options.body), { resource_key: 'gpt:first', group_ids: ['vla-1'] });
  assert.equal(request.options.method, 'PUT');
  assert.equal(app.nodes.get('batch-resource-dialog').open, true);
  assert.match(app.nodes.get('batch-resource-result').textContent, /分配已保存/);
  assert.deepEqual(right.inputs.filter(input => input.checked).map(input => input.value), ['vla-1']);
  assert.equal(vm.runInContext('groups[0].resource_keys.includes("coding-plan:one")', app.context), true);
  assert.equal(app.nodes.get('batch-resource-save').disabled, false);
});

test('bulk assignment retains unsaved choices on failed save or cancelled resource switch', async () => {
  const app = await page();
  app.nodes.get('batch-resources').listeners.click();
  chooseResource(app, 'gpt:first');
  app.nodes.get('batch-groups-none').listeners.click();
  app.state.confirm = false;
  chooseResource(app, 'gpt:second');
  assert.equal(vm.runInContext('batchResource', app.context), 'gpt:first');
  assert.equal(app.nodes.get('batch-resource-options').inputs.find(input => input.value === 'gpt:first').checked, true);
  app.state.saveError = true;
  await app.nodes.get('batch-resource-form').listeners.submit({ preventDefault() {} });
  assert.equal(app.nodes.get('batch-resource-error').textContent, '保存失败');
  assert.equal(app.nodes.get('batch-group-options').inputs.filter(input => input.checked).length, 0);
  assert.equal(vm.runInContext('groups[0].resource_keys.includes("gpt:first")', app.context), true);
  assert.equal(app.nodes.get('batch-resource-save').disabled, false);
});
