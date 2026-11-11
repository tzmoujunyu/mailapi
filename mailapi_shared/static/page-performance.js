(() => {
  const tables = new WeakMap();
  const scripts = new Map();

  function renderRows(root, rows) {
    const previous = tables.get(root);
    const next = new Map();
    for (const { key, html } of rows) {
      const saved = previous?.get(key);
      if (saved?.html === html && saved.nodes.every(node => node.parentNode === root)) {
        next.set(key, saved);
      } else {
        const container = document.createElement('tbody');
        container.innerHTML = html;
        next.set(key, { html, nodes: [...container.children] });
      }
    }
    if (!previous) root.replaceChildren();
    else for (const [key, saved] of previous) {
      if (next.get(key) !== saved) saved.nodes.forEach(node => node.remove());
    }
    let cursor = root.firstElementChild;
    for (const { nodes } of next.values()) for (const node of nodes) {
      if (node !== cursor) root.insertBefore(node, cursor);
      cursor = node.nextElementSibling;
    }
    tables.set(root, next);
  }

  function loadScript(src) {
    if (!scripts.has(src)) {
      const script = document.createElement('script');
      script.src = src;
      const loading = new Promise((resolve, reject) => {
        script.onload = resolve;
        script.onerror = () => { scripts.delete(src); script.remove(); reject(new Error('脚本加载失败')); };
      });
      scripts.set(src, loading);
      document.head.append(script);
    }
    return scripts.get(src);
  }

  window.PagePerformance = { renderRows, loadScript };
})();
