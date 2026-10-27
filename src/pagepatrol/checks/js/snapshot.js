() => {
  document.querySelectorAll('[data-pp-id]').forEach((el) => el.removeAttribute('data-pp-id'));

  const INTERACTIVE = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary',
    '[role=button]', '[role=link]', '[role=checkbox]', '[role=radio]', '[role=switch]',
    '[role=tab]', '[role=menuitem]', '[role=option]', '[role=combobox]', '[role=textbox]',
    '[contenteditable=""]', '[contenteditable=true]', '[onclick]',
  ].join(',');
  const MAX_ELEMENTS = 200;

  const clean = (text, max = 80) => (text || '').replace(/\s+/g, ' ').trim().slice(0, max);

  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
  };

  const INPUT_ROLES = {
    checkbox: 'checkbox', radio: 'radio', submit: 'button', button: 'button', reset: 'button',
    range: 'slider', number: 'spinbutton', search: 'searchbox',
  };
  const TAG_ROLES = { A: 'link', BUTTON: 'button', SELECT: 'combobox', TEXTAREA: 'textbox', SUMMARY: 'button' };
  const roleOf = (el) => {
    if (el.getAttribute('role')) return el.getAttribute('role');
    if (el.tagName === 'INPUT') return INPUT_ROLES[el.type] || 'textbox';
    return TAG_ROLES[el.tagName] || el.tagName.toLowerCase();
  };

  const labelOf = (el) => {
    if (el.labels && el.labels.length) return clean([...el.labels].map((l) => l.innerText).join(' '));
    const ids = el.getAttribute('aria-labelledby');
    if (ids) return clean(ids.split(/\s+/).map((id) => document.getElementById(id)?.innerText || '').join(' '));
    return '';
  };

  const nameOf = (el) => clean(
    el.getAttribute('aria-label')
    || labelOf(el)
    || (el.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes(el.type) ? el.value : '')
    || el.innerText
    || el.getAttribute('title')
    || el.getAttribute('alt')
    || el.querySelector('img[alt]')?.alt
    || el.getAttribute('placeholder'),
  );

  // Text around an element, to tell apart buttons that share a name, like
  // three "Add to cart" buttons on a product list.
  const contextOf = (el) => {
    const box = el.parentElement?.closest(
      'li, tr, article, section, form, fieldset, [class*=card], [class*=item], [class*=product]',
    );
    return box ? clean(box.innerText, 120) : '';
  };

  const elements = [];
  const nodes = [];
  for (const el of document.querySelectorAll(INTERACTIVE)) {
    if (elements.length >= MAX_ELEMENTS) break;
    if (!isVisible(el) || el.closest('[aria-hidden=true]')) continue;
    const id = elements.length + 1;
    el.setAttribute('data-pp-id', String(id));
    const item = { id, role: roleOf(el), name: nameOf(el) };
    if (el.placeholder) item.placeholder = clean(el.placeholder);
    if (el.disabled) item.disabled = true;
    if (['checkbox', 'radio'].includes(el.type)) item.checked = el.checked;
    const typed = ['INPUT', 'TEXTAREA'].includes(el.tagName)
      && !['password', 'submit', 'button', 'reset', 'checkbox', 'radio'].includes(el.type);
    if (typed && el.value) item.value = clean(el.value);
    if (el.tagName === 'SELECT') {
      item.value = clean(el.selectedOptions[0]?.text);
      item.options = [...el.options].slice(0, 20).map((o) => clean(o.text));
    }
    if (el.tagName === 'A') item.href = el.getAttribute('href');
    if (el.dataset.testid) item.testid = el.dataset.testid;
    elements.push(item);
    nodes.push(el);
  }

  const counts = {};
  for (const item of elements) counts[`${item.role}|${item.name}`] = (counts[`${item.role}|${item.name}`] || 0) + 1;
  elements.forEach((item, i) => {
    if (counts[`${item.role}|${item.name}`] > 1) item.context = contextOf(nodes[i]);
  });

  const text = (document.body?.innerText || '').replace(/\n\s*\n+/g, '\n').trim().slice(0, 4000);
  return { url: location.href, title: document.title, text, elements };
}
