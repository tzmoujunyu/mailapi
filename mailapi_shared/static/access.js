const roleNames = { user: '用户', manager: '普通管理员', admin: 'admin' };
const kindNames = { gpt: 'GPT', mailbox: '邮箱接码', 'coding-plan': 'Coding Plan', 'relay-account': '中转站' };
let currentUser = null;
let users = [];
let groups = [];
let resources = [];
let editingUser = null;
let editingGroup = null;
let loadVersion = 0;
const byId = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const tags = values => values.length ? values.map(value => `<span class="tag">${escapeHtml(value)}</span>`).join('') : '<span class="muted">暂无</span>';

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

function renderGroupOverview(memberTags, resourceNames) {
  const resourceByKey = new Map(resources.map(item => [item.key, item]));
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
        const accounts = group.resource_keys.map(key => resourceByKey.get(key)).filter(item => item?.kind === 'gpt');
        const summary = accounts.length ? `${escapeHtml(accounts[0].name)}${accounts.length > 1 ? ' <span class="muted">等多个账号</span>' : ''}` : '<span class="muted">暂无 GPT 账号</span>';
        const searchText = [group.name, number ? `组${number}` : '未编号', ...group.resource_keys.map(key => resourceNames.get(key) || '')].join(' ');
        return `<div class="matrix-cell" data-edit-group="${escapeHtml(group.id)}" data-search-text="${escapeHtml(searchText)}">
        <button type="button" class="matrix-edit" data-edit-group="${escapeHtml(group.id)}"
          title="编辑 ${escapeHtml(group.name)}" aria-label="编辑小组与成员 · ${escapeHtml(group.name)}">
          <span class="matrix-members">${group.member_ids.length ? memberTags(group) : '<span class="muted">暂无成员</span>'}</span>
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
    const actions = [`<a href="/preview?user_id=${encodeURIComponent(user.id)}">按此账号预览</a>`];
    if (user.role !== 'admin' && (currentUser.role === 'admin' || user.role === 'user')) actions.push(`<button type="button" data-edit-user="${escapeHtml(user.id)}">${currentUser.role === 'admin' ? '编辑' : '重置密码'}</button>`);
    if (currentUser.role === 'admin' && user.role !== 'admin') actions.push(`<button type="button" class="danger" data-delete-user="${escapeHtml(user.id)}">删除</button>`);
    return `<tr><td>${escapeHtml(user.username)}</td><td>${roleNames[user.role]}</td><td>${tags(groups.filter(group => group.member_ids.includes(user.id)).map(group => group.name))}</td><td><div class="actions">${actions.join('')}</div></td></tr>`;
  }).join('');
  const userNames = new Map(users.map(user => [user.id, user.username]));
  const memberTags = group => tags(group.member_ids.map(id => userNames.get(id) || '已删除账号'));
  const resourceNames = new Map(resources.map(item => [item.key, `${kindNames[item.kind]} · ${item.name}${item.listed === false ? '（已下架）' : ''}`]));
  byId('groups').innerHTML = groups.length ? groups.map(group => `<tr>
    <td>${escapeHtml(group.name)}</td><td>${memberTags(group)}</td>
    <td>${tags(group.resource_keys.map(key => resourceNames.get(key) || '已移除资源'))}</td>
    <td>${groupActions(group, '编辑成员与资源')}</td>
  </tr>`).join('') : '<tr><td colspan="4" class="muted">暂无分组，创建后即可分配成员和资源。</td></tr>';
  renderGroupOverview(memberTags, resourceNames);
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
    byId('add-group').disabled = false;
  } catch (error) {
    if (version !== loadVersion) return;
    byId('message').textContent = error.message;
    byId('message').className = 'error';
  }
}

function openUser(user = null) {
  editingUser = user;
  byId('user-form').reset();
  byId('user-title').textContent = user ? `编辑账号 · ${user.username}` : '创建账号';
  byId('username').value = user?.username || '';
  byId('username').disabled = Boolean(user);
  byId('user-role').value = user?.role || 'user';
  byId('user-role').disabled = Boolean(user) && currentUser.role !== 'admin';
  byId('user-password').required = !user;
  byId('password-help').textContent = user ? '至少 8 个字符，留空保留当前密码。重置后该账号需重新登录。' : '密码至少 8 个字符。';
  byId('user-error').textContent = '';
  byId('user-dialog').showModal();
}

function openGroup(group = null, { membersOnly = false, category = '', number = '1' } = {}) {
  editingGroup = group;
  byId('group-form').reset();
  const action = membersOnly ? '小组' : '分组';
  byId('group-title').textContent = group ? `编辑${action} · ${group.name}` : `创建${action}`;
  byId('group-name').value = group?.name || '';
  byId('group-name-field').hidden = membersOnly;
  byId('group-name').disabled = membersOnly;
  byId('group-position').hidden = !membersOnly;
  byId('group-position').disabled = !membersOnly;
  const position = group ? groupPosition(group.name) : { category, number };
  byId('group-category').value = position.category;
  byId('group-number').value = position.number;
  byId('group-category-options').innerHTML = [...new Set(groups.map(item => groupPosition(item.name).category))]
    .map(name => `<option value="${escapeHtml(name)}"></option>`).join('');
  byId('member-options').innerHTML = users.map(user => `<label class="option"><input type="checkbox" value="${escapeHtml(user.id)}" ${group?.member_ids.includes(user.id) ? 'checked' : ''}><span>${escapeHtml(user.username)} <small>${roleNames[user.role]}</small></span></label>`).join('');
  byId('group-resources').hidden = membersOnly;
  byId('group-config').classList.toggle('members-only', membersOnly);
  byId('group-dialog').classList.toggle('members-only', membersOnly);
  if (!membersOnly) {
    byId('resource-options').innerHTML = resources.length ? Object.entries(kindNames).map(([kind, label]) => {
      const candidates = resources.filter(item => item.kind === kind);
      if (!candidates.length) return '';
      return `<div class="resource-kind" data-resource-kind="${kind}"><h3>${kind === 'gpt' ? 'GPT 账号' : label}<span>${candidates.length}</span></h3>${candidates.map(item => {
        const shared = kind === 'gpt' ? groups.filter(candidate => candidate.resource_keys.includes(item.key)).length : 0;
        return `<label class="option"><input type="checkbox" value="${escapeHtml(item.key)}" ${group?.resource_keys.includes(item.key) ? 'checked' : ''}><span><span class="resource-option-name">${escapeHtml(item.name)}${item.listed === false ? ' <small>已下架</small>' : ''}</span>${kind === 'gpt' ? `<small class="resource-sharing" title="已分配至 ${shared} 个分组">${shared} Group shared</small>` : ''}</span></label>`;
      }).join('')}</div>`;
    }).join('') : '<p class="muted">暂无资源，请先到资源账号控制台添加。</p>';
  }
  byId('group-error').textContent = '';
  byId('group-dialog').showModal();
  if (membersOnly) byId(position.category ? 'group-number' : 'group-category').focus();
}

function openBatchUsers() {
  byId('batch-user-form').reset();
  byId('batch-user-error').textContent = '';
  byId('batch-user-dialog').showModal();
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
  if (byId('user-password').value) payload.password = byId('user-password').value;
  if (!editingUser) payload.username = byId('username').value;
  await api(editingUser ? `/api/admin/users/${encodeURIComponent(editingUser.id)}` : '/api/admin/users', jsonRequest(editingUser ? 'PUT' : 'POST', payload));
  byId('user-dialog').close();
  byId('message').className = '';
  byId('message').textContent = editingUser ? '账号已更新' : '账号已创建';
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
  const payload = { name, member_ids: checked('member-options') };
  if (!byId('group-resources').hidden) payload.resource_keys = checked('resource-options');
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
  byId('message').textContent = `已添加 ${usernames.length} 个账号`;
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
    remove(button, `/api/admin/users/${encodeURIComponent(user.id)}`, `删除网站账号“${user.username}”？其登录会话和分组成员身份将一并移除。`);
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
    openGroup(null, { membersOnly: true, category: add.dataset.category, number: add.dataset.number });
    return;
  }
  const edit = event.target.closest('[data-edit-group]');
  if (edit) {
    const group = groups.find(group => group.id === edit.dataset.editGroup);
    if (group) openGroup(group, { membersOnly: event.currentTarget.id === 'group-overview' });
  }
}
byId('groups').addEventListener('click', handleGroupAction);
byId('group-overview').addEventListener('click', handleGroupAction);
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => byId(button.dataset.close).close()));
byId('add-user').addEventListener('click', () => openUser());
byId('batch-users').addEventListener('click', openBatchUsers);
byId('add-group').addEventListener('click', () => openGroup());
byId('add-user').disabled = true;
byId('add-group').disabled = true;

window.addEventListener('focus', () => { if (!document.querySelector('dialog[open]')) load(); });
load();
