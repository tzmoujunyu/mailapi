(() => {
  const $ = id => document.getElementById(id);
  const api = async url => {
    const response = await fetch(url,{cache:'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  };
  let isAdmin = false;
  async function load() {
    await Promise.all([
      apiController.load(),
      financeController.load().catch(error => { $('dashboard-finance').textContent = '报销记录加载失败'; $('finance-status').textContent = error.message; }),
      isAdmin ? apiController.loadAdmin().catch(error => { $('api-key-status').textContent = error.message; }) : null,
    ]);
  }
  document.addEventListener('test:view-enter',load);
  api('/api/test-features/status').then(async status => {
    isAdmin = ['admin','manager'].includes(status.role);
    apiController.start(isAdmin);
    financeController.start(isAdmin);
    await load();
  }).catch(error => { $('api-key-status').textContent = error.message; });
})();
