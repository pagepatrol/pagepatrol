// Runs after load, once perf_observe.js has been collecting and helpers.js is installed.
() => {
  const { selectorFor, docRect } = window.__pagepatrol;
  const state = window.__pagepatrolPerf || { supported: [], longTasks: [] };
  const nav = performance.getEntriesByType('navigation')[0];
  const fcp = performance.getEntriesByName('first-contentful-paint')[0];
  const resources = performance.getEntriesByType('resource');
  const size = (r) => r.transferSize || r.encodedBodySize || 0;
  const where = (node) =>
    node && node.isConnected ? { selector: selectorFor(node), rect: docRect(node) } : null;

  const metrics = {
    ttfb: nav ? nav.responseStart : null,
    fcp: fcp ? fcp.startTime : null,
    load: nav && nav.loadEventEnd ? nav.loadEventEnd : null,
    lcp: state.lcp,
    cls: state.supported.includes('layout-shift') ? state.cls : null,
    // Total blocking time: how long the main thread was too busy to respond
    // to a tap, counting the part of each long task over 50ms after first paint.
    tbt: state.supported.includes('longtask')
      ? state.longTasks
          .filter(([start]) => !fcp || start >= fcp.startTime)
          .reduce((sum, [, duration]) => sum + Math.max(0, duration - 50), 0)
      : null,
    requests: resources.length + 1,
    transfer: resources.reduce((sum, r) => sum + size(r), nav ? size(nav) : 0),
  };

  const bySrc = new Map(resources.map((r) => [r.name, r]));
  const images = [];
  for (const img of document.images) {
    if (!img.complete || !img.naturalWidth || !img.currentSrc) continue;
    const rect = docRect(img);
    if (!rect) continue;
    const entry = bySrc.get(img.currentSrc);
    images.push({
      selector: selectorFor(img),
      rect,
      src: img.currentSrc,
      natural: [img.naturalWidth, img.naturalHeight],
      shown: [Math.round(rect.width * devicePixelRatio), Math.round(rect.height * devicePixelRatio)],
      bytes: (entry && size(entry)) || null,
    });
  }

  return {
    metrics,
    lcpElement: where(state.lcpNode),
    clsElement: where(state.clsNode),
    images,
    large: resources
      .filter((r) => size(r) > 500 * 1024)
      .map((r) => ({ url: r.name, type: r.initiatorType, bytes: size(r) })),
    blocking: resources
      .filter((r) => r.renderBlockingStatus === 'blocking')
      .map((r) => ({ url: r.name, type: r.initiatorType, duration: r.duration })),
  };
}
