const roleNames = { user: '用户', manager: '普通管理员', admin: 'admin' };
const kindNames = { gpt: 'GPT', 'coding-plan': 'Coding Plan', 'relay-account': '中转站' };
let currentUser = null;
let users = [];
let groups = [];
let resources = [];
let editingUser = null;
let editingGroup = null;
let loading = false;
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

function render() {
  byId('users').innerHTML = users.map(user => {
    const actions = [`<a href="/preview?user_id=${encodeURIComponent(user.id)}">按此账号预览</a>`];
    if (user.role !== 'admin' && (currentUser.role === 'admin' || user.role === 'user')) actions.push(`<button type="button" data-edit-user="${escapeHtml(user.id)}">${currentUser.role === 'admin' ? '编辑' : '重置密码'}</button>`);
    if (currentUser.role === 'admin' && user.role !== 'admin') actions.push(`<button type="button" class="danger" data-delete-user="${escapeHtml(user.id)}">删除</button>`);
    return `<tr><td>${escapeHtml(user.username)}</td><td>${roleNames[user.role]}</td><td>${tags(groups.filter(group => group.member_ids.includes(user.id)).map(group => group.name))}</td><td><div class="actions">${actions.join('')}</div></td></tr>`;
  }).join('');
  const userNames = new Map(users.map(user => [user.id, user.username]));
  const resourceNames = new Map(resources.map(item => [item.key, `${kindNames[item.kind]} · ${item.name}${item.listed === false ? '（已下架）' : ''}`]));
  byId('groups').innerHTML = groups.length ? groups.map(group => `<tr><td>${escapeHtml(group.name)}</td><td>${tags(group.member_ids.map(id => userNames.get(id) || '已删除账号'))}</td><td>${tags(group.resource_keys.map(key => resourceNames.get(key) || '已移除资源'))}</td><td><div class="actions"><button type="button" data-edit-group="${escapeHtml(group.id)}">编辑成员与资源</button><button type="button" class="danger" data-delete-group="${escapeHtml(group.id)}">删除分组</button></div></td></tr>`).join('') : '<tr><td colspan="4" class="muted">暂无分组，创建后即可分配成员和资源。</td></tr>';
}

async function load() {
  if (loading) return;
  loading = true;
  try {
    const [session, userData, groupData, resourceData] = await Promise.all([
      api('/api/auth/me'), api('/api/admin/users'), api('/api/admin/groups'), api('/api/admin/resources'),
    ]);
    currentUser = session.user;
    users = userData.items;
    groups = groupData.items;
    resources = resourceData.items;
    render();
    byId('add-user').disabled = false;
    byId('batch-users').disabled = currentUser.role !== 'admin';
    byId('batch-users').hidden = currentUser.role !== 'admin';
    byId('add-group').disabled = false;
  } catch (error) { byId('message').textContent = error.message; byId('message').className = 'error'; }
  finally { loading = false; }
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

function openGroup(group = null) {
  editingGroup = group;
  byId('group-form').reset();
  byId('group-title').textContent = group ? `编辑分组 · ${group.name}` : '创建分组';
  byId('group-name').value = group?.name || '';
  byId('member-options').innerHTML = users.map(user => `<label class="option"><input type="checkbox" value="${escapeHtml(user.id)}" ${group?.member_ids.includes(user.id) ? 'checked' : ''}><span>${escapeHtml(user.username)} <small>${roleNames[user.role]}</small></span></label>`).join('');
  byId('resource-options').innerHTML = resources.length ? resources.map(item => `<label class="option"><input type="checkbox" value="${escapeHtml(item.key)}" ${group?.resource_keys.includes(item.key) ? 'checked' : ''}><span><span class="tag">${kindNames[item.kind]}</span>${escapeHtml(item.name)}${item.listed === false ? ' <small>已下架</small>' : ''}</span></label>`).join('') : '<p class="muted">暂无资源，请先到资源账号控制台添加。</p>';
  byId('group-error').textContent = '';
  byId('group-dialog').showModal();
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
  const payload = { name: byId('group-name').value, member_ids: checked('member-options'), resource_keys: checked('resource-options') };
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
byId('groups').addEventListener('click', event => {
  const edit = event.target.closest('[data-edit-group]');
  if (edit) openGroup(groups.find(group => group.id === edit.dataset.editGroup));
  const button = event.target.closest('[data-delete-group]');
  if (button) {
    const group = groups.find(group => group.id === button.dataset.deleteGroup);
    remove(button, `/api/admin/groups/${encodeURIComponent(group.id)}`, `删除分组“${group.name}”？成员通过此分组获得的资源权限将立即取消。`);
  }
});
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => byId(button.dataset.close).close()));
byId('add-user').addEventListener('click', () => openUser());
byId('batch-users').addEventListener('click', openBatchUsers);
byId('add-group').addEventListener('click', () => openGroup());
byId('add-user').disabled = true;
byId('add-group').disabled = true;
window.addEventListener('focus', () => { if (!document.querySelector('dialog[open]')) load(); });
load();
