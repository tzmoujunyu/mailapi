(() => {
  const input = document.getElementById('management-search');
  if (!input) return;
  const clear = document.getElementById('management-search-clear');
  const status = document.getElementById('management-search-status');
  const results = document.getElementById('management-search-results');
  const normalize = value => String(value).normalize('NFKC').toLocaleLowerCase();
  const scopes = [...document.querySelectorAll('[data-search-scope]')].map(root => {
    const button = document.createElement('button');
    button.type = 'button';
    button.addEventListener('click', () => {
      const tab = document.getElementById(root.dataset.searchTab);
      if (tab) {
        tab.click();
        window.scrollTo({ top: Math.max(0, root.closest('table').getBoundingClientRect().top + window.scrollY
          - tab.closest('.management-tabs').offsetHeight - 12), behavior: 'smooth' });
      }
      else root.closest('section').scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
    results.append(button);
    return { root, button, index: null };
  });

  function filter() {
    const terms = normalize(input.value).trim().split(/\s+/).filter(Boolean);
    let total = 0;
    for (const scope of scopes) {
      const { root, button } = scope;
      const overview = root.hasAttribute('data-search-overview');
      const items = scope.index ||= [...root.querySelectorAll(overview ? '.matrix-cell' : ':scope > tr')]
        .filter(item => !item.hasAttribute('data-search-empty') && !item.querySelector('td[colspan]'))
        .map(item => ({ item, text: normalize(item.textContent + ' ' + (item.dataset.searchText || '')
          + ' ' + [...item.querySelectorAll('a[href]')].map(link => link.getAttribute('href')).join(' ')) }));
      let count = 0;
      for (const { item, text } of items) {
        const hidden = !terms.every(term => text.includes(term));
        if (item.hidden !== hidden) item.hidden = hidden;
        if (!item.hidden) count++;
      }
      if (overview) {
        root.querySelectorAll('tbody > tr').forEach(row => {
          row.hidden = terms.length > 0 && !row.querySelector('.matrix-cell:not([hidden])');
        });
        root.querySelectorAll('.matrix-data:not(.matrix-vacant)').forEach(cell => {
          cell.inert = terms.length > 0 && !cell.querySelector('.matrix-cell:not([hidden])');
        });
        document.getElementById('overview-search-empty').hidden = !terms.length || count > 0;
      } else {
        root.querySelectorAll(':scope > tr:not([data-search-empty])').forEach(row => {
          if (row.querySelector('td[colspan]')) row.hidden = terms.length > 0;
        });
        let empty = root.querySelector('[data-search-empty]');
        if (!empty && terms.length) {
          empty = document.createElement('tr');
          empty.setAttribute('data-search-empty', '');
          const cell = document.createElement('td');
          cell.colSpan = [...root.closest('table').querySelectorAll('thead tr:first-child th')].reduce((sum, heading) => sum + heading.colSpan, 0);
          cell.textContent = '没有匹配的内容';
          empty.append(cell);
          root.append(empty);
        }
        if (empty) empty.hidden = !terms.length || count > 0;
      }
      total += count;
      const label = `${root.dataset.searchScope} · ${count}`;
      if (button.textContent !== label) button.textContent = label;
      button.disabled = count === 0;
    }
    const message = terms.length ? (total ? `共 ${total} 项匹配，可点击下方模块查看` : '没有匹配的内容') : '搜索覆盖本页全部功能';
    if (status.textContent !== message) status.textContent = message;
    clear.hidden = results.hidden = !terms.length;
  }

  let timer;
  function schedule(delay = 0) { clearTimeout(timer); timer = setTimeout(filter, delay); }
  input.addEventListener('input', event => { if (!event.isComposing) schedule(200); });
  input.addEventListener('compositionend', () => schedule(200));
  function reset() { clearTimeout(timer); input.value = ''; filter(); input.focus(); }
  clear.addEventListener('click', reset);
  input.addEventListener('keydown', event => { if (event.key === 'Escape') reset(); });
  scopes.forEach(scope => {
    const observer = new MutationObserver(() => { scope.index = null; schedule(); });
    observer.observe(scope.root, { childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ['data-search-text', 'href'] });
  });
  filter();
})();
