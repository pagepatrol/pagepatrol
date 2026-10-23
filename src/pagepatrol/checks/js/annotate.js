(markers) => {
  const id = '__pagepatrol_markers__';
  document.getElementById(id)?.remove();
  if (!markers) return;

  const colors = { error: '#e5484d', warning: '#f5a524', info: '#3e8ef7' };
  const layer = document.createElement('div');
  layer.id = id;
  layer.style.cssText = 'position:absolute;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none;';

  for (const m of markers) {
    const color = colors[m.severity] || colors.info;
    const box = document.createElement('div');
    box.style.cssText =
      `position:absolute;left:${m.x}px;top:${m.y}px;width:${Math.max(m.width, 4)}px;height:${Math.max(m.height, 4)}px;` +
      `outline:2px solid ${color};outline-offset:1px;box-sizing:border-box;`;

    const label = document.createElement('span');
    label.textContent = String(m.n);
    label.style.cssText =
      `position:absolute;left:-3px;top:${m.y < 18 ? 2 : -17}px;min-width:14px;padding:0 3px;` +
      `font:600 10px/14px system-ui,sans-serif;color:#fff;background:${color};border-radius:3px;text-align:center;`;

    box.appendChild(label);
    layer.appendChild(box);
  }
  document.documentElement.appendChild(layer);
}
