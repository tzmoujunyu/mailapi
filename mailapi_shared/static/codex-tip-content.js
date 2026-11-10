(() => {
  const imagePath = /^\/api\/codex-tip-images\/[0-9a-f]{32}\.webp$/;
  const inlineTags = new Set(['B', 'STRONG', 'I', 'EM', 'CODE', 'BR', 'A']);

  function blocks(tip) {
    if (Array.isArray(tip?.blocks)) return tip.blocks;
    const node = document.createElement('div');
    node.textContent = tip?.body || '';
    return node.textContent ? [{ type: 'paragraph', data: { text: node.innerHTML.replaceAll('\n', '<br>') } }] : [];
  }

  function inline(target, value) {
    const template = document.createElement('template');
    template.innerHTML = value || '';
    function append(parent, source) {
      for (const child of source.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) parent.append(document.createTextNode(child.textContent));
        else if (inlineTags.has(child.nodeName)) {
          const node = document.createElement(child.nodeName.toLowerCase());
          if (child.nodeName === 'A') {
            const url = child.getAttribute('href') || '';
            if (!/^https?:\/\//i.test(url)) {
              parent.append(document.createTextNode(child.textContent));
              continue;
            }
            node.href = url;
            node.target = '_blank';
            node.rel = 'noopener noreferrer';
          }
          append(node, child);
          parent.append(node);
        } else parent.append(document.createTextNode(child.textContent));
      }
    }
    append(target, template.content);
  }

  function render(tip, root) {
    root.replaceChildren();
    function renderList(items, ordered, meta = {}) {
      const list = document.createElement(ordered ? 'ol' : 'ul');
      if (ordered && Number.isInteger(meta.start)) list.start = meta.start;
      if (ordered && ['1', 'a', 'A', 'i', 'I'].includes(meta.type)) list.type = meta.type;
      for (const item of items) {
        const li = document.createElement('li');
        inline(li, item.content);
        if (item.items?.length) li.append(renderList(item.items, item.style ? item.style === 'ordered' : ordered, item.meta));
        list.append(li);
      }
      return list;
    }
    for (const block of blocks(tip)) {
      const data = block.data || {};
      let node;
      if (block.type === 'paragraph' || block.type === 'header') {
        node = document.createElement(block.type === 'header' && data.level === 3 ? 'h3' : block.type === 'header' ? 'h2' : 'p');
        inline(node, data.text);
      } else if (block.type === 'code') {
        node = document.createElement('pre');
        const code = document.createElement('code');
        code.textContent = data.code || '';
        node.append(code);
      } else if (block.type === 'list') node = renderList(data.items || [], data.style === 'ordered', data.meta);
      else if (block.type === 'image' && imagePath.test(data.file?.url)) {
        node = document.createElement('figure');
        const link = document.createElement('a');
        link.href = data.file.url;
        link.dataset.tipImage = '';
        link.dataset.width = data.file.width;
        link.dataset.height = data.file.height;
        link.target = '_blank';
        link.rel = 'noopener';
        const image = document.createElement('img');
        image.src = data.file.url;
        image.width = data.file.width;
        image.height = data.file.height;
        image.alt = data.caption ? data.caption.replace(/<[^>]*>/g, '') : tip.title;
        image.loading = 'lazy';
        link.append(image);
        node.append(link);
        if (data.caption) {
          const caption = document.createElement('figcaption');
          inline(caption, data.caption);
          node.append(caption);
        }
      }
      if (node) root.append(node);
    }
  }

  function imageGallery(root, dialog) {
    let gallery = null;
    root.addEventListener('click', event => {
      const link = event.target.closest('[data-tip-image]');
      if (!link || !window.PhotoSwipe) return;
      event.preventDefault();
      const links = [...root.querySelectorAll('[data-tip-image]')];
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      gallery = new window.PhotoSwipe({
        dataSource: links.map(item => ({ src: item.href, width: Number(item.dataset.width), height: Number(item.dataset.height),
          alt: item.querySelector('img').alt })),
        index: links.indexOf(link), appendToEl: dialog, returnFocus: true,
        showAnimationDuration: reduced ? 0 : 160, hideAnimationDuration: reduced ? 0 : 160,
      });
      gallery.on('destroy', () => { gallery = null; link.focus(); });
      gallery.init();
    });
    dialog.addEventListener('cancel', event => {
      if (gallery) { event.preventDefault(); gallery.close(); }
    });
    dialog.addEventListener('close', () => { if (gallery) gallery.destroy(); });
  }

  window.CodexTipContent = { blocks, render, imageGallery };
})();
