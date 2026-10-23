() => {
  const MAX_ELEMENTS = 5000;
  const MAX_ITEMS = 6000;
  const MAX_PER_CHECK = 25;
  // Same threshold Lighthouse uses for "legible font sizes".
  const MIN_FONT_PX = 12;

  const findings = [];
  const totals = {};
  const add = (finding) => {
    totals[finding.check] = (totals[finding.check] || 0) + 1;
    if (totals[finding.check] <= MAX_PER_CHECK) findings.push(finding);
  };

  const viewportWidth = document.documentElement.clientWidth;
  const skipTags = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'BR', 'WBR']);
  const controlTags = new Set(['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA']);

  const toDoc = (r) => ({ x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height });
  const area = (r) => (r.right - r.left) * (r.bottom - r.top);

  const intersect = (a, b) => {
    const r = {
      left: Math.max(a.left, b.left),
      top: Math.max(a.top, b.top),
      right: Math.min(a.right, b.right),
      bottom: Math.min(a.bottom, b.bottom),
    };
    return r.right > r.left && r.bottom > r.top ? r : null;
  };

  const isVisible = (el) => {
    if (el.checkVisibility && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) {
      return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const { selectorFor } = window.__pagepatrol;

  // Where an element's content is visible, after every overflow:hidden/auto
  // ancestor (and the element itself) has clipped it. null means unclipped.
  const clipCache = new Map();
  const contentClip = (el) => {
    if (!el || el === document.documentElement || el === document.body) return null;
    if (clipCache.has(el)) return clipCache.get(el);
    let clip = contentClip(el.parentElement);
    const s = getComputedStyle(el);
    if (s.overflowX !== 'visible' || s.overflowY !== 'visible') {
      const r = el.getBoundingClientRect();
      const own = {
        left: s.overflowX !== 'visible' ? r.left : -Infinity,
        right: s.overflowX !== 'visible' ? r.right : Infinity,
        top: s.overflowY !== 'visible' ? r.top : -Infinity,
        bottom: s.overflowY !== 'visible' ? r.bottom : Infinity,
      };
      clip = clip ? intersect(clip, own) || { left: 0, top: 0, right: 0, bottom: 0 } : own;
    }
    clipCache.set(el, clip);
    return clip;
  };
  // Anything outside the document itself (skip links parked at top: -9999px
  // and the like) can't be seen, so clip everything to the document too.
  const root = document.documentElement;
  const documentBox = {
    left: -scrollX,
    top: -scrollY,
    right: root.scrollWidth - scrollX,
    bottom: root.scrollHeight - scrollY,
  };
  const clipped = (r, clip) => {
    const visibleArea = clip ? intersect(clip, documentBox) : documentBox;
    return visibleArea && intersect(r, visibleArea);
  };

  // Fixed and sticky elements move with scrolling, so overlaps with them
  // depend on scroll position and aren't reliable on a full-page capture.
  const fixedCache = new Map();
  const inFixedLayer = (el) => {
    if (!el || el === document.documentElement) return false;
    if (fixedCache.has(el)) return fixedCache.get(el);
    const pos = getComputedStyle(el).position;
    const result = pos === 'fixed' || pos === 'sticky' || inFixedLayer(el.parentElement);
    fixedCache.set(el, result);
    return result;
  };

  const elements = document.body ? [...document.body.querySelectorAll('*')].slice(0, MAX_ELEMENTS) : [];
  const items = [];
  const textElements = [];

  for (const el of elements) {
    const tag = el.tagName.toUpperCase();
    if (skipTags.has(tag)) continue;
    if (el instanceof SVGElement && el.ownerSVGElement) continue;
    if (!isVisible(el)) continue;

    if (controlTags.has(tag)) {
      const r = clipped(el.getBoundingClientRect(), contentClip(el.parentElement));
      if (r) items.push({ el, kind: 'control', rect: r });
    }

    let hasText = false;
    const clip = contentClip(el);
    for (const node of el.childNodes) {
      if (node.nodeType !== Node.TEXT_NODE || !node.textContent.trim()) continue;
      hasText = true;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const lineRect of range.getClientRects()) {
        const r = clipped(lineRect, clip);
        if (r && r.right - r.left > 1 && r.bottom - r.top > 1) items.push({ el, kind: 'text', rect: r });
      }
    }
    if (hasText) textElements.push(el);
    if (items.length > MAX_ITEMS) break;
  }

  // Overlapping text and controls. Sweep along x so we only compare boxes
  // that can actually touch.
  items.sort((a, b) => a.rect.left - b.rect.left);
  const seenPairs = new Set();
  const ids = new Map();
  const idOf = (el) => {
    if (!ids.has(el)) ids.set(el, ids.size);
    return ids.get(el);
  };
  for (let i = 0; i < items.length; i++) {
    const a = items[i];
    for (let j = i + 1; j < items.length && items[j].rect.left < a.rect.right; j++) {
      const b = items[j];
      if (a.el === b.el) continue;
      const overlap = intersect(a.rect, b.rect);
      if (!overlap) continue;
      const w = overlap.right - overlap.left;
      const h = overlap.bottom - overlap.top;
      if (w <= 2 || h <= 2 || w * h < 0.2 * Math.min(area(a.rect), area(b.rect))) continue;
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      if (inFixedLayer(a.el) || inFixedLayer(b.el)) continue;

      const key = [idOf(a.el), idOf(b.el)].sort((x, y) => x - y).join(':');
      if (seenPairs.has(key)) continue;
      seenPairs.add(key);

      const bothText = a.kind === 'text' && b.kind === 'text';
      const union = {
        left: Math.min(a.rect.left, b.rect.left),
        top: Math.min(a.rect.top, b.rect.top),
        right: Math.max(a.rect.right, b.rect.right),
        bottom: Math.max(a.rect.bottom, b.rect.bottom),
      };
      add({
        check: 'overlap',
        category: 'layout',
        severity: bothText ? 'error' : 'warning',
        message: bothText ? 'Text overlaps other text' : `A ${a.kind} overlaps a ${b.kind}`,
        selector: selectorFor(a.el),
        rect: toDoc({ left: union.left, top: union.top, width: union.right - union.left, height: union.bottom - union.top }),
        details: { other: selectorFor(b.el), overlap_px: [Math.round(w), Math.round(h)] },
      });
    }
  }

  for (const el of textElements) {
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (r.width <= 2 || r.height <= 2) continue;

    const clipsX = s.overflowX === 'hidden' || s.overflowX === 'clip';
    const clipsY = s.overflowY === 'hidden' || s.overflowY === 'clip';
    const lineClamp = s.webkitLineClamp && s.webkitLineClamp !== 'none';
    const cutX = clipsX && s.textOverflow !== 'ellipsis' && el.scrollWidth > el.clientWidth + 1;
    const cutY = clipsY && !lineClamp && el.scrollHeight > el.clientHeight + 1;
    if (cutX || cutY) {
      // Several lines shown and then cut is usually a deliberate excerpt;
      // a single cut line is usually a label that doesn't fit.
      const lineHeight = parseFloat(s.lineHeight) || parseFloat(s.fontSize) * 1.2;
      const lines = Math.round(el.clientHeight / lineHeight);
      const excerpt = !cutX && lines >= 2;
      add({
        check: 'clipped-text',
        category: 'readability',
        severity: excerpt ? 'info' : 'warning',
        message: excerpt
          ? `Text is cut off after ${lines} lines (may be an intentional excerpt)`
          : 'Text is cut off by its container',
        selector: selectorFor(el),
        rect: toDoc(r),
        details: { content_px: [el.scrollWidth, el.scrollHeight], visible_px: [el.clientWidth, el.clientHeight] },
      });
    }

    const size = parseFloat(s.fontSize);
    if (size < MIN_FONT_PX) {
      add({
        check: 'small-text',
        category: 'readability',
        severity: 'warning',
        message: `Text is ${+size.toFixed(1)}px, below the ${MIN_FONT_PX}px minimum for legible text`,
        selector: selectorFor(el),
        rect: toDoc(r),
        details: { font_size_px: size },
      });
    }
  }

  const pageWidth = document.documentElement.scrollWidth;
  if (pageWidth > viewportWidth + 1) {
    let culprits = 0;
    for (const el of elements) {
      if (!isVisible(el) || inFixedLayer(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.right + scrollX <= viewportWidth + 1) continue;
      const parent = el.parentElement;
      const parentOverflows =
        parent && parent !== document.body && parent.getBoundingClientRect().right + scrollX > viewportWidth + 1;
      if (parentOverflows) continue;
      const clip = contentClip(parent);
      if (clip && clip.right <= viewportWidth + 1) continue;
      culprits++;
      add({
        check: 'horizontal-overflow',
        category: 'responsive',
        severity: 'error',
        message: `Element sticks out ${Math.round(r.right + scrollX - viewportWidth)}px past the right edge and makes the page scroll sideways`,
        selector: selectorFor(el),
        rect: toDoc(r),
        details: { page_width: pageWidth, viewport_width: viewportWidth },
      });
    }
    if (!culprits) {
      add({
        check: 'horizontal-overflow',
        category: 'responsive',
        severity: 'error',
        message: `Page is ${pageWidth}px wide in a ${viewportWidth}px viewport and scrolls sideways`,
        selector: null,
        rect: null,
        details: { page_width: pageWidth, viewport_width: viewportWidth },
      });
    }
  }

  for (const img of document.images) {
    if (!img.complete || img.naturalWidth > 0 || !img.getAttribute('src') || !isVisible(img)) continue;
    add({
      check: 'broken-image',
      category: 'content',
      severity: 'error',
      message: 'Image failed to load',
      selector: selectorFor(img),
      rect: toDoc(img.getBoundingClientRect()),
      details: { src: img.currentSrc || img.src },
    });
  }

  const links = [...document.querySelectorAll('a[href]')].map((a) => ({
    href: a.href,
    selector: selectorFor(a),
    rect: isVisible(a) ? toDoc(a.getBoundingClientRect()) : null,
  }));

  return { findings, totals, links, pageWidth, viewportWidth };
}
