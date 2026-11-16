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
    const managementLink = document.body.classList.contains('test-ui-page')
      ? ''
      : '<a href="/admin/access" data-management hidden>网站账户与分组</a>';
    bar.innerHTML = `<strong>账户中心</strong><span data-identity></span><span data-groups></span>${managementLink}<div class="site-session-actions"><span data-theme-control></span><button type="button" data-password>修改密码</button><button type="button" data-logout>退出登录</button></div>`;
    bar.querySelector('[data-identity]').textContent = `${user.username} · ${roleNames[user.role]}`;
    bar.querySelector('[data-groups]').textContent = groups.length ? `分组：${groups.map(group => group.name).join('、')}` : '暂未加入分组';
    const management = bar.querySelector('[data-management]');
    if (management) management.hidden = user.role === 'user';
    document.body.prepend(bar);
    window.SiteTheme?.mount(bar.querySelector('[data-theme-control]'));
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
    dialog.innerHTML = '<form><h2>修改本人密码</h2><p data-initial-password hidden>你的账户仍在使用初始密码，请设置一个新密码。</p><label>当前密码<input name="current_password" type="password" autocomplete="current-password" required></label><label>新密码（8–128 字符）<input name="new_password" type="password" autocomplete="new-password" required></label><p class="site-password-error" role="alert"></p><div class="site-password-actions"><button type="button" data-cancel>取消</button><button type="submit">保存并重新登录</button></div></form>';
    document.body.append(dialog);
    dialog.querySelector('[data-initial-password]').hidden = !user.must_change_password;
    const passwordButton = bar.querySelector('[data-password]');
    if (user.must_change_password) passwordButton.textContent = '修改初始密码';
    const openPasswordDialog = () => {
      dialog.querySelector('form').reset();
      dialog.querySelector('[role="alert"]').textContent = '';
      dialog.showModal();
    };
    passwordButton.addEventListener('click', openPasswordDialog);
    dialog.querySelector('[data-cancel]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => dialog.querySelector('form').reset());
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
    const passwordLanding = document.body.classList.contains('test-ui-page')
      ? location.pathname === '/test-features'
      : location.pathname === '/';
    if (user.must_change_password && passwordLanding) openPasswordDialog();
  }
  start().catch(() => {});
})();
