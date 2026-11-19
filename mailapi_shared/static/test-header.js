(() => {
  const header = document.querySelector('.test-ui-header');
  if (!header) return;
  const actions = header.querySelector('.test-ui-header-actions');
  const icon = name => `<svg aria-hidden="true" viewBox="0 0 24 24"><use href="/static/test-navigation.svg#${name}"></use></svg>`;
  actions.insertAdjacentHTML('afterbegin', `<button class="header-icon" id="header-appearance" type="button" aria-label="主题设置">${icon('palette')}</button>`);
  const presets = [
    ['default', '默认', '#4f46e5', '#6366f1'],
    ['anthropic', 'Anthropic', '#d97757', '#faf9f5'],
    ['simple-large', '超大字体简易', '#18181b', '#71717a'],
    ['underground', '暗夜', '#4d7661', '#88627f'],
    ['rose-garden', '玫瑰花园', '#d91857', '#ff97b5'],
    ['lake-view', '湖光', '#059669', '#0c7f8c'],
    ['sunset-glow', '日落霞光', '#d54238', '#ff986c'],
    ['forest-whisper', '森林低语', '#008478', '#526d83'],
    ['ocean-breeze', '海风', '#2563eb', '#6366f1'],
    ['lavender-dream', '薰衣草梦', '#9560c8', '#95c8cc'],
  ];
  let saved = {theme: document.documentElement.dataset.theme || 'system', preset: 'default', font: 'auto', radius: 'auto'};
  const defaults = {theme: 'system', preset: 'default', font: 'auto', radius: 'auto'};
  const drawer = document.createElement('dialog');
  drawer.className = 'appearance-drawer';
  drawer.setAttribute('aria-labelledby', 'appearance-title');
  function choices(key, values, preview) {
    return `<fieldset data-setting="${key}"><legend>${({theme: '主题', preset: '颜色预设', font: '字体', radius: '圆角'})[key]}</legend><div class="appearance-choices choices-${key}">${values.map(([value, label]) => `<label class="appearance-choice"><input type="radio" name="${key}" value="${value}"><span class="choice-preview ${key}-${value.replace('.', '-')}">${preview(value)}</span><span>${label}</span></label>`).join('')}</div></fieldset>`;
  }
  drawer.innerHTML = `<div class="appearance-heading"><div><h2 id="appearance-title">主题设置</h2><p>调整外观和布局以适应您的偏好。</p></div><button type="button" class="header-icon" data-close aria-label="关闭">×</button></div><form class="appearance-form">
    ${choices('theme', [['system', '系统'], ['light', '浅色'], ['dark', '深色']], () => '<i></i><b></b><em></em>')}
    ${choices('preset', presets.map(([value, label]) => [value, label]), value => { const preset = presets.find(row => row[0] === value); return `<i style="background:linear-gradient(135deg,${preset[2]},${preset[3]})"></i>`; })}
    ${choices('font', [['auto', 'Auto'], ['sans', 'Sans'], ['serif', 'Serif']], () => 'Aa')}
    ${choices('radius', [['auto', 'Auto'], ['0', '0'], ['0.3', '0.3'], ['0.5', '0.5'], ['0.75', '0.75'], ['1', '1.0']], value => `<i style="border-top-left-radius:${value === 'auto' ? '16px' : value + 'rem'}"></i>`)}
    </form><div class="appearance-footer"><p role="status" aria-live="polite"></p><button class="appearance-reset" type="button">重置</button></div>`;
  document.body.append(drawer);
  const status = drawer.querySelector('[role="status"]');
  function apply(settings) {
    const root = document.documentElement;
    root.dataset.theme = settings.theme;
    root.dataset.uiPreset = settings.preset;
    root.dataset.uiFont = settings.font === 'auto' && settings.preset === 'anthropic' ? 'serif' : settings.font;
    root.dataset.uiRadius = settings.radius;
    const selected = presets.find(row => row[0] === settings.preset) || presets[0];
    root.style.setProperty('--ui-accent', selected[2]);
    root.style.setProperty('--ui-accent-light', selected[3]);
    if (settings.radius === 'auto') root.style.removeProperty('--ui-radius');
    else root.style.setProperty('--ui-radius', settings.radius + 'rem');
    for (const input of drawer.querySelectorAll('input')) input.checked = settings[input.name] === input.value;
  }
  function lock(value) {
    for (const field of drawer.querySelectorAll('fieldset')) field.disabled = value;
    drawer.querySelector('.appearance-reset').disabled = value;
  }
  async function save(patch) {
    lock(true);
    const next = {...saved, ...patch};
    apply(next);
    status.textContent = '';
    try {
      const response = await fetch('/api/test-ui/preferences', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(patch)});
      const result = await response.json();
      if (!response.ok) throw new Error('外观保存失败，请重试');
      saved = result;
      apply(saved);
    } catch (error) {
      apply(saved);
      status.textContent = error.message;
    } finally { lock(false); }
  }
  drawer.addEventListener('change', event => {
    if (event.target.matches('input[type="radio"]')) save({[event.target.name]: event.target.value});
  });
  drawer.querySelector('form').addEventListener('submit', event => event.preventDefault());
  drawer.querySelector('[data-close]').addEventListener('click', () => drawer.close());
  drawer.addEventListener('click', event => { if (event.target === drawer) drawer.close(); });
  drawer.querySelector('.appearance-reset').addEventListener('click', () => save(defaults));
  header.querySelector('#header-appearance').addEventListener('click', () => drawer.showModal());
  apply(saved);
  lock(true);
  fetch('/api/test-ui/preferences', {cache: 'no-store'}).then(async response => {
    if (!response.ok) throw new Error('外观设置加载失败，请重试');
    saved = await response.json();
    apply(saved);
  }).catch(error => { status.textContent = error.message; }).finally(() => lock(false));
})();
