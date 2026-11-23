(() => {
  const form = document.getElementById('login-form');
  const challenge = document.getElementById('login-challenge');
  if (!form || !challenge) return;
  const error = form.querySelector('.error');
  const submit = form.querySelector('[type="submit"]');
  const status = document.getElementById('login-challenge-status');
  const retry = document.getElementById('login-challenge-retry');
  const text = value => window.SiteLanguage?.t(value) || value;
  let required = challenge.dataset.required === '1';
  let token = '', widget, loading, busy = false;

  form.elements.username.addEventListener('input', () => {
    required = false;
    token = '';
    error.textContent = '';
    challenge.hidden = true;
    if (widget !== undefined) {
      window.turnstile.remove(widget);
      widget = undefined;
    }
  });

  function unavailable() {
    token = '';
    status.textContent = text('安全验证暂时不可用，请稍后重试');
    retry.hidden = false;
  }

  function loadProvider() {
    if (window.turnstile) return Promise.resolve();
    if (loading) return loading;
    loading = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      const timer = setTimeout(() => fail(), 10000);
      function fail() {
        clearTimeout(timer);
        script.remove();
        loading = null;
        reject(new Error('verification_unavailable'));
      }
      window.mailapiTurnstileReady = () => { clearTimeout(timer); resolve(); };
      script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?onload=mailapiTurnstileReady&render=explicit';
      script.async = true;
      script.onerror = fail;
      document.head.append(script);
    });
    return loading;
  }

  async function showChallenge() {
    const username = form.elements.username.value;
    required = true;
    challenge.hidden = false;
    token = '';
    retry.hidden = true;
    status.textContent = text('正在加载安全验证…');
    try {
      await loadProvider();
      if (!required || form.elements.username.value !== username) return;
      if (widget !== undefined) {
        window.turnstile.reset(widget);
        return;
      }
      widget = window.turnstile.render('#login-challenge-widget', {
        sitekey: challenge.dataset.sitekey,
        action: 'login',
        size: 'flexible',
        theme: 'auto',
        language: document.documentElement.lang === 'en' ? 'en' : 'zh-cn',
        callback: value => {
          if (!required || form.elements.username.value !== username) return;
          token = value; status.textContent = text('安全验证已完成，请点击登录'); retry.hidden = true;
        },
        'expired-callback': () => {
          if (!required || form.elements.username.value !== username) return;
          token = ''; status.textContent = text('安全验证已过期，请重新验证'); retry.hidden = false;
        },
        'timeout-callback': () => { if (required && form.elements.username.value === username) unavailable(); },
        'error-callback': () => { if (required && form.elements.username.value === username) unavailable(); return true; },
      });
    } catch {
      if (required && form.elements.username.value === username) unavailable();
    }
  }

  retry.addEventListener('click', showChallenge);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    if (required && !token) {
      error.textContent = text('请先完成安全验证');
      return;
    }
    busy = true;
    form.elements.username.readOnly = true;
    submit.disabled = true;
    form.setAttribute('aria-busy', 'true');
    error.textContent = '';
    const requestToken = token;
    token = '';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const values = new FormData(form);
      const response = await fetch('/api/auth/login', {
        method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: values.get('username'), password: values.get('password'),
          totp_code: values.get('totp_code') || null, turnstile_token: requestToken || null}),
      });
      let payload;
      try { payload = await response.json(); } catch { payload = {}; }
      if (response.ok && typeof payload.redirect === 'string' && payload.redirect.startsWith('/') && !payload.redirect.startsWith('//')) {
        form.reset();
        token = '';
        location.assign(payload.redirect);
        return;
      }
      error.textContent = text(typeof payload?.detail === 'string' ? payload.detail : '登录未完成，请稍后重试');
      if (payload.challenge || required) await showChallenge();
    } catch {
      error.textContent = text('网络连接失败，请检查网络后重试');
      if (required) await showChallenge();
    } finally {
      clearTimeout(timer);
      busy = false;
      form.elements.username.readOnly = false;
      submit.disabled = false;
      form.removeAttribute('aria-busy');
    }
  });
  if (required) showChallenge();
})();
