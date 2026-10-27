// Runs before the page's own scripts, so nothing that happens during load is missed.
(() => {
  const state = { lcp: null, lcpNode: null, cls: 0, clsNode: null, longTasks: [], supported: [] };
  window.__pagepatrolPerf = state;

  const observe = (type, onEntry) => {
    // Firefox accepts unknown types without complaint, so ask first.
    if (!(PerformanceObserver.supportedEntryTypes || []).includes(type)) return;
    try {
      new PerformanceObserver((list) => list.getEntries().forEach(onEntry)).observe({
        type,
        buffered: true,
      });
      state.supported.push(type);
    } catch (e) {
      // Not supported in this browser.
    }
  };

  observe('largest-contentful-paint', (entry) => {
    state.lcp = entry.startTime;
    state.lcpNode = entry.element || null;
  });

  // CLS the way Chrome counts it: the worst burst of shifts, where a burst
  // ends after a 1s pause or 5s in total.
  let window_ = { value: 0, first: 0, last: 0, node: null, biggest: 0 };
  observe('layout-shift', (entry) => {
    if (entry.hadRecentInput) return;
    const t = entry.startTime;
    if (window_.value && t - window_.last < 1000 && t - window_.first < 5000) {
      window_.value += entry.value;
      window_.last = t;
    } else {
      window_ = { value: entry.value, first: t, last: t, node: null, biggest: 0 };
    }
    if (entry.value > window_.biggest) {
      window_.biggest = entry.value;
      window_.node = (entry.sources || []).map((s) => s.node).find((n) => n && n.nodeType === 1) || null;
    }
    if (window_.value > state.cls) {
      state.cls = window_.value;
      state.clsNode = window_.node;
    }
  });

  observe('longtask', (entry) => state.longTasks.push([entry.startTime, entry.duration]));
})();
