const roleNames = { user: '用户', manager: '普通管理员', admin: 'admin' };
const kindNames = { gpt: 'GPT', mailbox: '邮箱接码', 'coding-plan': 'Coding Plan', 'relay-account': '中转站' };
let currentUser = null;
let users = [];
let groups = [];
let resources = [];
let editingUser = null;
let editingGroup = null;
let batchResource = null;
let batchSaving = false;
let loadVersion = 0;
const byId = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const tags = values => values.length ? values.map(value => `<span class="tag">${escapeHtml(value)}</span>`).join('') : '<span class="muted" data-ui-text>暂无</span>';

async function api(url, options = {}) {
  const response = await fetch(url, { cache: 'no-store', ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败，请检查输入');
  return data;
}
const jsonRequest = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function groupActions(group, editLabel) {
  const id = escapeHtml(group.id);
  return `<div class="actions">
    <button type="button" data-edit-group="${id}">${editLabel}</button>
    <button type="button" class="danger" data-delete-group="${id}">删除分组</button>
  </div>`;
}

function groupPosition(name) {
  const numbered = /^(.+)-(\d+)$/.exec(name);
  return { category: numbered ? numbered[1].trim() : name, number: numbered ? numbered[2] : '' };
}

function nextGroupNumber(numbers) {
  let number = 1;
  while (numbers.has(String(number))) number += 1;
  return String(number);
}

function addGroupButton(category, number, label, showLabel = false) {
  return `<button type="button" class="matrix-add" data-create-group
    data-category="${escapeHtml(category)}" data-number="${escapeHtml(number)}"
    title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}"><span aria-hidden="true">＋</span>
    ${showLabel ? `<span class="matrix-add-label">${escapeHtml(label)}</span>` : ''}</button>`;
}

function renderGroupOverview(groupMembers, resourceNames) {
  const resourceByKey = new Map(resources.map(item => [item.key, item]));
  const now = new Date();
  const expiryByKey = new Map(resources.filter(item => item.kind === 'gpt').map(item => [item.key,
    subscriptionExpiry(item.subscription_expires_at, item.subscription_expires_on, now),
  ]));
  const categories = new Map();
  const numbers = new Set();
  let unnumbered = false;
  const sorted = [...groups].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN', { numeric: true, sensitivity: 'base' }));
  for (const group of sorted) {
    const { category, number } = groupPosition(group.name);
    const key = category.toLowerCase();
    if (!categories.has(key)) categories.set(key, { name: category, cells: new Map() });
    const cells = categories.get(key).cells;
    if (!cells.has(number)) cells.set(number, []);
    cells.get(number).push(group);
    if (number) numbers.add(number);
    else unnumbered = true;
  }

  const columns = [...numbers].sort((a, b) => a.localeCompare(b, 'en', { numeric: true }) || a.localeCompare(b));
  if (unnumbered) columns.push('');
  if (!columns.length) columns.push('1');
  const headings = columns.map(number => `<th class="matrix-heading" scope="col">${number ? `组${escapeHtml(number)}` : '未编号'}</th>`).join('');
  const rows = [...categories.values()].map(({ name, cells }) => {
    const contents = columns.map(number => {
      const matches = cells.get(number);
      if (!matches) {
        return `<td class="matrix-data matrix-vacant">${addGroupButton(name, number, `创建 ${name} ${number ? '组' + number : '未编号小组'}`)}</td>`;
      }
      const cellContents = matches.map(group => {
        const members = groupMembers(group);
        const accounts = group.resource_keys.map(key => resourceByKey.get(key)).filter(item => item?.kind === 'gpt');
        let nearest;
        for (const account of accounts) {
          const expiry = expiryByKey.get(account.key);
          if (expiry && (!nearest || expiry.end < nearest.expiry.end)) nearest = { account, expiry };
        }
        const account = nearest?.account || accounts[0];
        const expiryTag = nearest ? ` <span class="expiry-tag${nearest.expiry.due ? ' expiry-due' : ''}">(${nearest.expiry.remaining})</span>` : '';
        const summary = account ? `${escapeHtml(account.name)}${expiryTag}` : '<span class="muted" data-ui-text>暂无 GPT 账号</span>';
        const searchText = [group.name, number ? `组${number}` : '未编号', ...members.map(user => user.username), ...group.resource_keys.map(key => resourceNames.get(key) || '')].join(' ');
        return `<div class="matrix-cell" data-edit-group="${escapeHtml(group.id)}" data-search-text="${escapeHtml(searchText)}">
        <button type="button" class="matrix-edit" data-edit-group="${escapeHtml(group.id)}"
          title="编辑 ${escapeHtml(group.name)}" aria-label="编辑小组、成员与资源 · ${escapeHtml(group.name)}">
          <span class="matrix-members">${members.length ? tags(members.map(user => user.note || user.username)) : '<span class="muted" data-ui-text>暂无成员</span>'}</span>
          <span class="matrix-account">${summary}</span>
        </button>
        <button type="button" class="matrix-delete" data-delete-group="${escapeHtml(group.id)}"
          title="删除 ${escapeHtml(group.name)}" aria-label="删除分组 · ${escapeHtml(group.name)}">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6M14 10v6"></path>
          </svg>
        </button>
      </div>`;
      }).join('');
      const edit = matches.length === 1 ? `data-edit-group="${escapeHtml(matches[0].id)}"` : '';
      return `<td class="matrix-data" ${edit}>${cellContents}</td>`;
    }).join('');
    return `<tr><th class="matrix-category" scope="row">${escapeHtml(name)}</th>${contents}
      <td class="matrix-edge">${addGroupButton(name, nextGroupNumber(new Set(cells.keys())), `向 ${name} 添加小组`)}</td></tr>`;
  }).join('');
  byId('group-overview').innerHTML = `<thead><tr><th class="matrix-corner" scope="col">类别 / 小组</th>${headings}
    <th class="matrix-edge" scope="col">${addGroupButton('', nextGroupNumber(numbers), '新增小组列')}</th></tr></thead>
    <tbody>${rows || `<tr><td colspan="${columns.length + 2}" class="matrix-empty muted">暂无分组，点击下方 ＋ 创建第一个类别和小组。</td></tr>`}</tbody>
    <tfoot><tr><td class="matrix-new-category" colspan="${columns.length + 2}">
      ${addGroupButton('', '1', '新增类别', true)}
    </td></tr></tfoot>`;
}

function render() {
  byId('users').innerHTML = users.map(user => {
    const actions = [`<a href="/preview?user_id=${encodeURIComponent(user.id)}">按此账户预览</a>`];
    if (currentUser.role === 'admin' || user.role === 'user') actions.push(`<button type="button" data-edit-user="${escapeHtml(user.id)}">${user.role === 'admin' ? '编辑备注' : '编辑'}</button>`);
    if (currentUser.role === 'admin' && user.role !== 'admin') actions.push(`<button type="button" class="danger" data-delete-user="${escapeHtml(user.id)}">删除</button>`);
    const displayName = user.note ? `${user.username}（${user.note}）` : user.username;
    const passwordStatus = user.must_change_password ? ' <small class="muted" data-ui-text>需修改初始密码</small>' : '';
    return `<tr><td>${escapeHtml(displayName)}${passwordStatus}</td><td><span data-ui-text>${roleNames[user.role]}</span></td><td>${tags(groups.filter(group => group.member_ids.includes(user.id)).map(group => group.name))}</td><td><div class="actions">${actions.join('')}</div></td></tr>`;
  }).join('');
  const usersById = new Map(users.map(user => [user.id, user]));
  const groupMembers = group => group.member_ids.map(id => usersById.get(id) || { username: '已删除账户' });
  const resourceNames = new Map(resources.map(item => [item.key, `${kindNames[item.kind]} · ${item.name}${item.listed === false ? '（已下架）' : ''}`]));
  byId('groups').innerHTML = groups.length ? groups.map(group => `<tr>
    <td>${escapeHtml(group.name)}</td><td>${tags(groupMembers(group).map(user => user.username))}</td>
    <td>${tags(group.resource_keys.map(key => resourceNames.get(key) || '已移除资源'))}</td>
    <td>${groupActions(group, '编辑成员与资源')}</td>
  </tr>`).join('') : '<tr><td colspan="4" class="muted" data-ui-text>暂无分组，创建后即可分配成员和资源。</td></tr>';
  renderGroupOverview(groupMembers, resourceNames);
}

async function load() {
  const version = ++loadVersion;
  try {
    const [session, userData, groupData, resourceData] = await Promise.all([
      api('/api/auth/me'), api('/api/admin/users'), api('/api/admin/groups'), api('/api/admin/resources'),
    ]);
    if (version !== loadVersion) return;
    currentUser = session.user;
    users = userData.items;
    groups = groupData.items;
    resources = resourceData.items;
    render();
    byId('add-user').disabled = false;
    byId('batch-users').disabled = currentUser.role !== 'admin';
    byId('batch-users').hidden = currentUser.role !== 'admin';
    byId('scan-initial-password').disabled = currentUser.role !== 'admin';
    byId('scan-initial-password').hidden = currentUser.role !== 'admin';
    byId('add-group').disabled = false;
    byId('batch-resources').disabled = false;
  } catch (error) {
    if (version !== loadVersion) return;
    byId('message').textContent = error.message;
    byId('message').className = 'error';
  }
}

function openUser(user = null) {
  editingUser = user;
  byId('user-form').reset();
  const reserved = user?.role === 'admin';
  byId('user-title').textContent = user ? `编辑账户 · ${user.username}` : '创建账户';
  byId('username').value = user?.username || '';
  byId('username').disabled = Boolean(user);
  byId('user-note').value = user?.note || '';
  byId('user-role').value = user?.role || 'user';
  byId('user-role-field').hidden = reserved;
  byId('user-role').disabled = reserved || (Boolean(user) && currentUser.role !== 'admin');
  byId('user-password-field').hidden = byId('user-password').disabled = reserved;
  byId('user-password').required = !user;
  byId('password-help').textContent = user ? '8–128 个字符，留空保留当前密码。重置后该账户需重新登录。' : '密码须为 8–128 个字符。';
  byId('user-error').textContent = '';
  byId('user-dialog').showModal();
}

function openGroup(group = null, { fromOverview = false, category = '', number = '1' } = {}) {
  editingGroup = group;
  byId('group-form').reset();
  const action = fromOverview ? '小组' : '分组';
  byId('group-title').textContent = group ? `编辑${action} · ${group.name}` : `创建${action}`;
  byId('group-name').value = group?.name || '';
  byId('group-name-field').hidden = fromOverview;
  byId('group-name').disabled = fromOverview;
  byId('group-position').hidden = !fromOverview;
  byId('group-position').disabled = !fromOverview;
  const position = group ? groupPosition(group.name) : { category, number };
  byId('group-category').value = position.category;
  byId('group-number').value = position.number;
  byId('group-category-options').innerHTML = [...new Set(groups.map(item => groupPosition(item.name).category))]
    .map(name => `<option value="${escapeHtml(name)}"></option>`).join('');
  byId('member-options').innerHTML = users.map(user => `<label class="option"><input type="checkbox" value="${escapeHtml(user.id)}" ${group?.member_ids.includes(user.id) ? 'checked' : ''}><span>${escapeHtml(user.username)} <small data-ui-text>${roleNames[user.role]}</small></span></label>`).join('');
  const now = new Date();
  byId('resource-options').innerHTML = resources.length ? Object.entries(kindNames).map(([kind, label]) => {
    const candidates = resources.filter(item => item.kind === kind);
    if (!candidates.length) return '';
    return `<div class="resource-kind" data-resource-kind="${kind}"><h3>${kind === 'gpt' ? 'GPT 账号' : label}<span>${candidates.length}</span></h3>${candidates.map(item => {
      const shared = kind === 'gpt' ? groups.filter(candidate => candidate.resource_keys.includes(item.key)).length : 0;
      const expiry = kind === 'gpt' ? subscriptionExpiry(item.subscription_expires_at, item.subscription_expires_on, now) : null;
      const expiryTag = expiry ? ` <span class="expiry-tag${expiry.due ? ' expiry-due' : ''}" title="订阅到期 ${expiry.date}">(${expiry.remaining})</span>` : '';
      return `<label class="option"><input type="checkbox" value="${escapeHtml(item.key)}" ${group?.resource_keys.includes(item.key) ? 'checked' : ''}><span><span class="resource-option-name">${escapeHtml(item.name)}${expiryTag}${item.listed === false ? ' <small data-ui-text>已下架</small>' : ''}</span>${kind === 'gpt' ? `<small class="resource-sharing" title="已分配至 ${shared} 个分组">${shared} Group shared</small>` : ''}</span></label>`;
    }).join('')}</div>`;
  }).join('') : '<p class="muted">暂无资源，请先到资源账号控制台添加。</p>';
  byId('group-error').textContent = '';
  byId('group-dialog').showModal();
  if (fromOverview) byId(position.category ? 'group-number' : 'group-category').focus();
}

function batchGroupIds() {
  return [...byId('batch-group-options').querySelectorAll('input:checked')].map(input => input.value);
}

function updateBatchGroupCount() {
  const shown = [...byId('batch-group-options').querySelectorAll('.option')].filter(option => !option.hidden).length;
  const matches = byId('batch-group-search').value.trim() ? `匹配 ${shown} / ${groups.length} · ` : '';
  byId('batch-group-count').textContent = batchResource ? `${matches}已选 ${batchGroupIds().length} / ${groups.length} 个分组` : '';
  byId('batch-resource-result').textContent = '';
}

function filterBatchOptions(kind) {
  const normalize = value => value.normalize('NFKC').toLocaleLowerCase();
  const terms = normalize(byId(`batch-${kind}-search`).value).trim().split(/\s+/).filter(Boolean);
  const root = byId(`batch-${kind}-options`);
  const options = [...root.querySelectorAll('.option')];
  for (const option of options) {
    const text = normalize(option.textContent + ' ' + (option.dataset.searchText || ''));
    option.hidden = !terms.every(term => text.includes(term));
  }
  root.querySelectorAll('.resource-kind').forEach(section => { section.hidden = !section.querySelector('.option:not([hidden])'); });
  const shown = options.filter(option => !option.hidden).length;
  byId(`batch-${kind}-empty`).hidden = !terms.length || !options.length || shown > 0;
  if (kind === 'resource') byId('batch-resource-count').textContent = terms.length ? `匹配 ${shown} / ${options.length} 个资源` : `共 ${options.length} 个资源`;
  else updateBatchGroupCount();
}

for (const kind of ['resource', 'group']) {
  byId(`batch-${kind}-search`).addEventListener('input', () => filterBatchOptions(kind));
  byId(`batch-${kind}-search`).addEventListener('keydown', event => { if (event.key === 'Enter') event.preventDefault(); });
}

function renderBatchGroups() {
  const resource = resources.find(item => item.key === batchResource);
  byId('batch-current-resource').textContent = resource ? `${kindNames[resource.kind]} · ${resource.name}${resource.listed === false ? '（已下架）' : ''}` : '请先选择左侧资源。';
  byId('batch-group-fields').disabled = !resource;
  byId('batch-resource-save').disabled = !resource || !groups.length;
  byId('batch-group-options').innerHTML = resource
    ? groups.map(group => `<label class="option"><input type="checkbox" value="${escapeHtml(group.id)}" ${group.resource_keys.includes(batchResource) ? 'checked' : ''}><span>${escapeHtml(group.name)}</span></label>`).join('') || '<p class="muted">暂无分组，请先创建分组。</p>'
    : '<p class="muted" data-ui-text>选择资源后显示当前分配情况。</p>';
  filterBatchOptions('group');
}

function openBatchResources() {
  batchResource = null;
  byId('batch-resource-form').reset();
  byId('batch-resource-error').textContent = '';
  byId('batch-resource-options').innerHTML = resources.length ? Object.entries(kindNames).map(([kind, label]) => {
    const candidates = resources.filter(item => item.kind === kind);
    if (!candidates.length) return '';
    return `<div class="resource-kind"><h3>${kind === 'gpt' ? 'GPT 账号' : label}<span>${candidates.length}</span></h3>${candidates.map(item => `<label class="option" data-search-text="${escapeHtml(kind === 'gpt' ? 'GPT 账号' : label)}"><input type="radio" name="batch-resource" value="${escapeHtml(item.key)}"><span>${escapeHtml(item.name)}${item.listed === false ? ' <small data-ui-text>已下架</small>' : ''}</span></label>`).join('')}</div>`;
  }).join('') : '<p class="muted">暂无资源，请先到资源账号控制台添加。</p>';
  filterBatchOptions('resource');
  renderBatchGroups();
  byId('batch-resource-dialog').showModal();
}

byId('batch-resource-options').addEventListener('change', event => {
  const selected = event.target;
  if (!selected.matches('input[type="radio"]') || selected.value === batchResource) return;
  const saved = new Set(groups.filter(group => group.resource_keys.includes(batchResource)).map(group => group.id));
  const checked = batchGroupIds();
  const changed = batchResource && (saved.size !== checked.length || checked.some(id => !saved.has(id)));
  if (changed && !confirm('当前资源的分配尚未保存，切换将放弃这些修改。继续切换？')) {
    byId('batch-resource-options').querySelectorAll('input').forEach(input => { input.checked = input.value === batchResource; });
    return;
  }
  batchResource = selected.value;
  byId('batch-resource-error').textContent = '';
  renderBatchGroups();
});
byId('batch-group-options').addEventListener('change', updateBatchGroupCount);
for (const [id, checked] of [['batch-groups-all', true], ['batch-groups-none', false]]) {
  byId(id).addEventListener('click', () => {
    byId('batch-group-options').querySelectorAll('.option').forEach(option => {
      if (!option.hidden) option.querySelector('input').checked = checked;
    });
    updateBatchGroupCount();
  });
}
byId('batch-resource-dialog').addEventListener('cancel', event => { if (batchSaving) event.preventDefault(); });
byId('batch-resource-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!batchResource || batchSaving) return;
  batchSaving = true;
  loadVersion += 1;
  const payload = { resource_key: batchResource, group_ids: batchGroupIds() };
  for (const id of ['batch-resource-fields', 'batch-group-fields', 'batch-resource-save', 'batch-resource-close']) byId(id).disabled = true;
  byId('batch-resource-error').textContent = '';
  byId('batch-resource-result').textContent = '';
  try {
    const data = await api('/api/admin/resource-groups', jsonRequest('PUT', payload));
    groups = data.items;
    render();
    renderBatchGroups();
    byId('batch-resource-result').textContent = '分配已保存，可继续选择其他资源。';
  } catch (error) { byId('batch-resource-error').textContent = error.message; }
  finally {
    batchSaving = false;
    for (const id of ['batch-resource-fields', 'batch-group-fields', 'batch-resource-save', 'batch-resource-close']) byId(id).disabled = false;
  }
});

function openBatchUsers() {
  byId('batch-user-form').reset();
  byId('batch-user-error').textContent = '';
  byId('batch-user-dialog').showModal();
}

function openInitialPasswordScan() {
  byId('initial-password-form').reset();
  byId('initial-password-error').textContent = '';
  byId('initial-password-result').textContent = '';
  byId('initial-password-dialog').showModal();
}

function batchUsernames(value) {
  return [...new Set(value.split(/[\s,，;；]+/).map(item => item.trim().toLowerCase()).filter(Boolean))];
}

async function submitForm(event, errorId, action) {
  event.preventDefault();
  const button = event.target.querySelector('[type="submit"]');
  button.disabled = true;
  byId(errorId).textContent = '';
  try { await action(); await load(); }
  catch (error) { byId(errorId).textContent = error.message; }
  finally { button.disabled = false; }
}

byId('user-form').addEventListener('submit', event => submitForm(event, 'user-error', async () => {
  const payload = {};
  if (!byId('user-role').disabled) payload.role = byId('user-role').value;
  if (!byId('user-password').disabled && byId('user-password').value) payload.password = byId('user-password').value;
  if (!editingUser) payload.username = byId('username').value;
  const note = byId('user-note').value.trim();
  if (!editingUser || note !== (editingUser.note || '')) payload.note = note;
  await api(editingUser ? `/api/admin/users/${encodeURIComponent(editingUser.id)}` : '/api/admin/users', jsonRequest(editingUser ? 'PUT' : 'POST', payload));
  byId('user-dialog').close();
  byId('message').className = '';
  byId('message').textContent = editingUser ? '账户已更新' : '账户已创建';
}));

byId('group-form').addEventListener('submit', event => submitForm(event, 'group-error', async () => {
  const checked = id => [...byId(id).querySelectorAll('input:checked')].map(input => input.value);
  let name = byId('group-name').value;
  if (!byId('group-position').disabled) {
    const category = byId('group-category').value.trim();
    const number = byId('group-number').value.trim();
    if (!category) throw new Error('请输入类别名称');
    const original = editingGroup && groupPosition(editingGroup.name);
    name = original && original.category === category && original.number === number
      ? editingGroup.name : category + (number ? `-${number}` : '');
  }
  const payload = { name, member_ids: checked('member-options'), resource_keys: checked('resource-options') };
  await api(editingGroup ? `/api/admin/groups/${encodeURIComponent(editingGroup.id)}` : '/api/admin/groups', jsonRequest(editingGroup ? 'PUT' : 'POST', payload));
  byId('group-dialog').close();
  byId('message').className = '';
  byId('message').textContent = '分组已保存';
}));

byId('batch-user-form').addEventListener('submit', event => submitForm(event, 'batch-user-error', async () => {
  const usernames = batchUsernames(byId('batch-usernames').value);
  await api('/api/admin/users/batch', jsonRequest('POST', {
    usernames,
    role: byId('batch-user-role').value,
    password: byId('batch-user-password').value,
  }));
  byId('batch-user-dialog').close();
  byId('message').className = '';
  byId('message').textContent = `已添加 ${usernames.length} 个账户`;
}));

byId('initial-password-form').addEventListener('submit', event => submitForm(event, 'initial-password-error', async () => {
  const result = await api('/api/admin/users/initial-password-scan', jsonRequest('POST', {
    password: byId('initial-password').value,
  }));
  byId('initial-password-result').textContent = result.matched_count
    ? `已匹配 ${result.matched_count} 个账户，并标记为需修改密码。`
    : '没有匹配账户。';
  await load();
}));

async function remove(button, url, description) {
  if (!confirm(description)) return;
  button.disabled = true;
  try {
    await api(url, { method: 'DELETE' });
    byId('message').className = '';
    byId('message').textContent = '已删除';
    await load();
  } catch (error) { byId('message').className = 'error'; byId('message').textContent = error.message; }
  finally { button.disabled = false; }
}

byId('users').addEventListener('click', event => {
  const edit = event.target.closest('[data-edit-user]');
  if (edit) openUser(users.find(user => user.id === edit.dataset.editUser));
  const button = event.target.closest('[data-delete-user]');
  if (button) {
    const user = users.find(user => user.id === button.dataset.deleteUser);
    remove(button, `/api/admin/users/${encodeURIComponent(user.id)}`, `删除网站账户“${user.username}”？其登录会话和分组成员身份将一并移除。`);
  }
});
function handleGroupAction(event) {
  const button = event.target.closest('[data-delete-group]');
  if (button) {
    const group = groups.find(group => group.id === button.dataset.deleteGroup);
    if (group) remove(button, `/api/admin/groups/${encodeURIComponent(group.id)}`, `删除分组“${group.name}”？成员通过此分组获得的资源权限将立即取消。`);
    return;
  }
  const add = event.target.closest('[data-create-group]');
  if (add) {
    openGroup(null, { fromOverview: true, category: add.dataset.category, number: add.dataset.number });
    return;
  }
  const edit = event.target.closest('[data-edit-group]');
  if (edit) {
    const group = groups.find(group => group.id === edit.dataset.editGroup);
    if (group) openGroup(group, { fromOverview: event.currentTarget.id === 'group-overview' });
  }
}
byId('groups').addEventListener('click', handleGroupAction);
byId('group-overview').addEventListener('click', handleGroupAction);
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => byId(button.dataset.close).close()));
byId('add-user').addEventListener('click', () => openUser());
byId('batch-users').addEventListener('click', openBatchUsers);
byId('scan-initial-password').addEventListener('click', openInitialPasswordScan);
byId('add-group').addEventListener('click', () => openGroup());
byId('batch-resources').addEventListener('click', openBatchResources);
byId('add-user').disabled = true;
byId('add-group').disabled = true;
byId('batch-resources').disabled = true;
byId('scan-initial-password').disabled = true;

window.addEventListener('focus', () => { if (!document.querySelector('dialog[open]')) load(); });
load();
