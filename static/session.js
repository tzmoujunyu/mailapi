(() => {
  const roleNames = { user: '用户', manager: '普通管理员', admin: 'admin' };
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    if (response.status === 401) window.location.assign('/login');
    return response;
  };

  async function start() {
    const response = await fetch('/api/auth/me', { cache: 'no-store' });
    if (!response.ok) return;
    const { user, groups } = await response.json();
    const bar = document.createElement('nav');
    bar.className = 'site-session';
    bar.setAttribute('aria-label', '网站会话');
    bar.innerHTML = '<strong>账号中心</strong><span data-identity></span><span data-groups></span><a href="/admin/access" data-management hidden>网站账号与分组</a><div class="site-session-actions"><button type="button" data-password>修改密码</button><button type="button" data-logout>退出登录</button></div>';
    bar.querySelector('[data-identity]').textContent = `${user.username} · ${roleNames[user.role]}`;
    bar.querySelector('[data-groups]').textContent = groups.length ? `分组：${groups.map(group => group.name).join('、')}` : '暂未加入分组';
    bar.querySelector('[data-management]').hidden = user.role === 'user';
    document.body.prepend(bar);
    bar.querySelector('[data-logout]').addEventListener('click', async event => {
      event.target.disabled = true;
      try {
        const result = await fetch('/api/auth/logout', { method: 'POST' });
        if (!result.ok) throw new Error('退出失败，请重试');
        window.location.assign('/login');
      } catch (error) { event.target.disabled = false; event.target.textContent = error.message; }
    });

    const dialog = document.createElement('dialog');
    dialog.className = 'site-password';
    dialog.innerHTML = '<form><h2>修改本人密码</h2><label>当前密码<input name="current_password" type="password" autocomplete="current-password" required maxlength="256"></label><label>新密码<input name="new_password" type="password" autocomplete="new-password" required minlength="8" maxlength="256"></label><p class="site-password-error" role="alert"></p><div class="site-password-actions"><button type="button" data-cancel>取消</button><button type="submit">保存并重新登录</button></div></form>';
    document.body.append(dialog);
    bar.querySelector('[data-password]').addEventListener('click', () => {
      dialog.querySelector('form').reset();
      dialog.querySelector('[role="alert"]').textContent = '';
      dialog.showModal();
    });
    dialog.querySelector('[data-cancel]').addEventListener('click', () => dialog.close());
    dialog.querySelector('form').addEventListener('submit', async event => {
      event.preventDefault();
      const submit = dialog.querySelector('[type="submit"]');
      submit.disabled = true;
      try {
        const result = await fetch('/api/auth/password', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(Object.fromEntries(new FormData(event.target))),
        });
        const data = await result.json();
        if (!result.ok) throw new Error(data.detail || '修改失败');
        window.location.assign('/login');
      } catch (error) { dialog.querySelector('[role="alert"]').textContent = error.message; }
      finally { submit.disabled = false; }
    });
  }
  start().catch(() => {});
})();
