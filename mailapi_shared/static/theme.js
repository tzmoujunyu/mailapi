(() => {
  const root = document.documentElement;
  const modes = ['system', 'light', 'dark'];
  const names = { system: '跟随系统', light: '浅色模式', dark: '深色模式' };
  const icons = {
    system: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
    light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42"/>',
    dark: '<path d="M20.9 13A9 9 0 0 1 11 3.1 9 9 0 1 0 20.9 13Z"/>',
  };
  let count = 0;

  function mount(holder) {
    holder.classList.add('site-theme');
    const tooltipId = `site-theme-tooltip-${++count}`;
    holder.innerHTML = `<span class="site-theme-button-wrap"><button class="site-theme-button" type="button" aria-describedby="${tooltipId}"></button><span id="${tooltipId}" class="site-theme-tooltip" role="tooltip"><span data-current-mode></span><span data-next-mode></span></span></span><span class="site-theme-status" role="status" aria-live="polite"></span>`;
    const button = holder.querySelector('button');
    const status = holder.querySelector('[role="status"]');
    let saving = false;

    function render(theme) {
      const next = modes[(modes.indexOf(theme) + 1) % modes.length];
      button.dataset.mode = theme;
      button.innerHTML = `<svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${icons[theme]}</svg>`;
      button.setAttribute('aria-label', `当前：${names[theme]}，点击切换为${names[next]}`);
      holder.querySelector('[data-current-mode]').textContent = `当前：${names[theme]}`;
      holder.querySelector('[data-next-mode]').textContent = `点击切换为${names[next]}`;
    }

    async function apply(theme) {
      const update = () => { root.dataset.theme = theme; render(theme); };
      if (document.startViewTransition && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        try {
          const transition = document.startViewTransition(update);
          transition.finished.catch(() => {});
          await transition.updateCallbackDone;
        } catch { update(); }
      } else update();
    }

    render(modes.includes(root.dataset.theme) ? root.dataset.theme : 'system');
    button.addEventListener('click', async () => {
      if (saving) return;
      const previous = root.dataset.theme || 'system';
      const theme = modes[(modes.indexOf(previous) + 1) % modes.length];
      status.textContent = '';
      saving = true;
      button.setAttribute('aria-disabled', 'true');
      try {
        await apply(theme);
        if (!root.dataset.themeAccount) return;
        const response = await fetch('/api/auth/preferences', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ theme }),
        });
        const data = await response.json();
        if (!response.ok || !modes.includes(data.theme)) throw new Error('外观保存失败，请重试');
        if (data.theme !== theme) await apply(data.theme);
      } catch {
        await apply(previous);
        status.textContent = '外观保存失败，请重试';
      } finally {
        saving = false;
        button.setAttribute('aria-disabled', 'false');
      }
    });
  }

  window.SiteTheme = { mount };
  document.querySelectorAll('[data-theme-control]').forEach(mount);
})();
