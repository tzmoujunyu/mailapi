/* Account-scoped, non-sensitive view settings. Search text never enters this API. */
(() => {
  const cache = new Map(), pending = new Map(), writing = new Set();
  function state(scope) {
    if (!cache.has(scope)) cache.set(scope, {settings:{},loaded:false,loading:null});
    return cache.get(scope);
  }
  async function read(scope) {
    const entry=state(scope);
    if (!entry.loaded && !entry.loading) entry.loading=fetch('/api/view-preferences/'+encodeURIComponent(scope), {cache:'no-store'})
      .then(response => {if (!response.ok) throw new Error('页面偏好加载失败');return response.json();})
      .then(data => {entry.settings={...data.settings,...entry.settings};entry.loaded=true;})
      .catch(() => {}).finally(() => {entry.loading=null;});
    await entry.loading;
    return {...entry.settings};
  }
  function save(scope, settings) {
    const previous = pending.get(scope);
    if (previous) clearTimeout(previous.timer);
    const entry = {settings:{...previous?.settings,...settings}};
    entry.timer = setTimeout(() => flush(scope), 450);
    pending.set(scope, entry);
    const cached=state(scope);cached.settings={...cached.settings,...settings};
  }
  async function flush(scope, keepalive=false) {
    const entry = pending.get(scope);if (!entry) return;
    entry.keepalive ||= keepalive;
    if (writing.has(scope)) return;
    clearTimeout(entry.timer);pending.delete(scope);
    writing.add(scope);
    try {
      const response = await fetch('/api/view-preferences/'+encodeURIComponent(scope), {method:'PUT',keepalive:entry.keepalive || false,
        headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:entry.settings})});
      if (!response.ok) throw new Error('页面偏好保存失败');
    } catch {
      document.dispatchEvent(new CustomEvent('preferences:error', {detail:{scope}}));
    } finally {
      writing.delete(scope);
      if (pending.has(scope)) flush(scope);
    }
  }
  window.addEventListener('pagehide',()=>{for(const scope of pending.keys())flush(scope,true);});
  window.ViewPreferences = {read,save};
})();
