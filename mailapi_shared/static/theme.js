(() => {
  const root = document.documentElement;
  const modes = ['system', 'light', 'dark'];

  function mount(holder) {
    holder.classList.add('site-theme');
    holder.innerHTML = '<label><span>外观</span><select aria-label="网页外观"><option value="system">跟随系统</option><option value="light">浅色模式</option><option value="dark">深色模式</option></select></label><span class="site-theme-status" role="status" aria-live="polite"></span>';
    const select = holder.querySelector('select');
    const status = holder.querySelector('[role="status"]');
    select.value = modes.includes(root.dataset.theme) ? root.dataset.theme : 'system';
    select.addEventListener('change', async () => {
      const previous = root.dataset.theme || 'system';
      root.dataset.theme = select.value;
      status.textContent = '';
      if (!root.dataset.themeAccount) return;
      select.disabled = true;
      try {
        const response = await fetch('/api/auth/preferences', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ theme: select.value }),
        });
        const data = await response.json();
        if (!response.ok || !modes.includes(data.theme)) throw new Error('外观保存失败，请重试');
        root.dataset.theme = select.value = data.theme;
      } catch {
        root.dataset.theme = select.value = previous;
        status.textContent = '外观保存失败，请重试';
      } finally { select.disabled = false; }
    });
  }

  window.SiteTheme = { mount };
  document.querySelectorAll('[data-theme-control]').forEach(mount);
})();
