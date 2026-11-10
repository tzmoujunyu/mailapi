(() => {
  const imagePath = /^\/api\/codex-tip-images\/[0-9a-f]{32}\.webp$/;
  const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
  const listType = style => style === 'ordered' ? 'orderedList' : 'bulletList';
  const listStyle = type => type === 'orderedList' ? 'ordered' : 'unordered';
  const listMeta = node => ({ ...(node.attrs?.start !== undefined && node.attrs.start !== 1 ? { start: node.attrs.start } : {}),
    ...(node.attrs?.type && node.attrs.type !== '1' ? { type: node.attrs.type } : {}) });

  function inlineNodes(html) {
    const template = document.createElement('template');
    template.innerHTML = html || '';
    function read(parent, marks = []) {
      return [...parent.childNodes].flatMap(node => {
        if (node.nodeType === Node.TEXT_NODE) return node.textContent ? [{ type: 'text', text: node.textContent, marks }] : [];
        if (node.nodeName === 'BR') return [{ type: 'hardBreak' }];
        const types = { B: 'bold', STRONG: 'bold', I: 'italic', EM: 'italic', CODE: 'code' };
        const next = [...marks];
        if (types[node.nodeName]) next.push({ type: types[node.nodeName] });
        if (node.nodeName === 'A' && /^https?:\/\//i.test(node.getAttribute('href') || '')) {
          next.push({ type: 'link', attrs: { href: node.getAttribute('href') } });
        }
        return read(node, next);
      });
    }
    return read(template.content);
  }

  function inlineHTML(nodes = []) {
    return nodes.map(node => {
      if (node.type === 'hardBreak') return '<br>';
      let text = escape(node.text || '');
      for (const mark of node.marks || []) {
        const tag = { bold: 'b', italic: 'i', code: 'code' }[mark.type];
        if (tag) text = `<${tag}>${text}</${tag}>`;
        else if (mark.type === 'link') text = `<a href="${escape(mark.attrs.href)}">${text}</a>`;
      }
      return text;
    }).join('');
  }

  function toDocument(tip) {
    function list(items, style, meta = {}) {
      return { type: listType(style), attrs: meta, content: items.map(item => ({ type: 'listItem', content: [
        { type: 'paragraph', content: inlineNodes(item.content) },
        ...(item.items?.length ? [list(item.items, item.style || style, item.meta)] : []),
      ] })) };
    }
    const content = window.CodexTipContent.blocks(tip).map(block => {
      const data = block.data;
      if (block.type === 'paragraph') return { type: 'paragraph', content: inlineNodes(data.text) };
      if (block.type === 'header') return { type: 'heading', attrs: { level: data.level }, content: inlineNodes(data.text) };
      if (block.type === 'code') return { type: 'codeBlock', content: data.code ? [{ type: 'text', text: data.code }] : [] };
      if (block.type === 'list') return list(data.items, data.style, data.meta);
      if (block.type === 'image' && imagePath.test(data.file?.url)) return { type: 'tipImage', attrs: { file: data.file }, content: [{ type: 'tipCaption', content: inlineNodes(data.caption) }] };
      throw new Error('提示中包含不支持的内容格式');
    });
    return { type: 'doc', content: content.length ? content : [{ type: 'paragraph' }] };
  }

  function fromDocument(doc) {
    function items(node) {
      return node.content.map(item => {
        const nested = item.content[1];
        return { content: inlineHTML(item.content[0].content), meta: nested ? listMeta(nested) : {}, items: nested ? items(nested) : [],
          ...(nested && nested.type !== node.type ? { style: listStyle(nested.type) } : {}) };
      });
    }
    return doc.content.map(node => {
      if (node.type === 'paragraph') return { type: 'paragraph', data: { text: inlineHTML(node.content) } };
      if (node.type === 'heading') return { type: 'header', data: { text: inlineHTML(node.content), level: node.attrs.level } };
      if (node.type === 'codeBlock') return { type: 'code', data: { code: (node.content || []).map(item => item.text || '').join('') } };
      if (node.type === 'bulletList' || node.type === 'orderedList') return { type: 'list', data: { style: listStyle(node.type), meta: listMeta(node), items: items(node) } };
      if (node.type === 'tipImage') return { type: 'image', data: { file: node.attrs.file, caption: inlineHTML(node.content[0].content) } };
      throw new Error('提示中包含不支持的内容格式');
    });
  }

  window.CodexTipFormat = { toDocument, fromDocument };
})();
