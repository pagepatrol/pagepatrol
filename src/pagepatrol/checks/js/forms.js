// Runs after snapshot.js, which tags interactive elements with data-pp-id.
() => {
  const clean = (text) => (text || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const id = (el) => (el && el.dataset.ppId ? Number(el.dataset.ppId) : null);
  const SKIP_TYPES = ['hidden', 'file', 'submit', 'button', 'reset', 'image'];

  const forms = [];
  const submitIds = [];
  for (const form of document.querySelectorAll('form')) {
    const submit = form.querySelector(
      'button[type=submit], button:not([type]), input[type=submit], input[type=image]',
    );
    if (submit && id(submit)) submitIds.push(id(submit));

    const fields = [];
    for (const el of form.querySelectorAll('input, select, textarea')) {
      if (SKIP_TYPES.includes(el.type) || el.disabled || el.readOnly || !id(el)) continue;
      const field = {
        id: id(el),
        tag: el.tagName.toLowerCase(),
        type: el.type || '',
        name: el.name || el.id || '',
        autocomplete: el.getAttribute('autocomplete') || '',
        label: clean(el.labels?.[0]?.innerText || el.getAttribute('aria-label') || el.placeholder),
        required: el.required,
        min: el.getAttribute('min'),
        max: el.getAttribute('max'),
        maxlength: el.maxLength > 0 ? el.maxLength : null,
        pattern: el.getAttribute('pattern'),
      };
      if (el.tagName === 'SELECT') {
        field.options = [...el.options].map((o) => ({ text: clean(o.text), value: o.value }));
      }
      fields.push(field);
    }
    if (!fields.length) continue;
    forms.push({
      name: clean(form.getAttribute('aria-label') || form.getAttribute('name') || form.id
        || form.querySelector('legend, h1, h2, h3')?.innerText || ''),
      action: form.getAttribute('action') || '',
      method: (form.getAttribute('method') || 'get').toLowerCase(),
      submit: id(submit),
      submitName: submit ? clean(submit.innerText || submit.value || submit.getAttribute('aria-label')) : '',
      fields,
    });
  }
  return { forms, submitIds };
}
