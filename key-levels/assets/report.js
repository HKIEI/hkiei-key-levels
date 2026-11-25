(() => {
  'use strict';
  const D = JSON.parse(document.getElementById('chart-data').textContent);
  const box = document.getElementById('chart');
  const readout = document.getElementById('readout');
  const periods = document.getElementById('periods');
  const NS = 'http://www.w3.org/2000/svg';
  const colors = {support: '#087e83', resistance: '#b9491e', at_price: '#7c709c'};
  const visible = {support: true, resistance: true, at_price: true};
  let days = Math.min(D.days, D.bars.length);
  const fmt = value => value.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  function el(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (parent) parent.appendChild(node);
    return node;
  }
  function text(parent, x, y, content, attrs = {}) {
    const node = el('text', {x, y, fill: '#657083', 'font-size': 11, ...attrs}, parent);
    node.textContent = content;
    return node;
  }
  function ticks(lo, hi) {
    const raw = (hi - lo) / 5;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(n => n * mag).find(n => n >= raw);
    const values = [];
    for (let n = Math.ceil(lo / step) * step; n <= hi + step * 1e-6; n += step) values.push(n);
    return values;
  }
  function updateReadout(bar) {
    readout.replaceChildren();
    const date = document.createElement('strong');
    date.textContent = bar.date;
    readout.appendChild(date);
    if (!bar.complete) {
      const pending = document.createElement('span');
      pending.className = 'incomplete'; pending.textContent = '未收市'; readout.appendChild(pending);
    }
    for (const [key, label] of [['open', '開'], ['high', '高'], ['low', '低'], ['close', bar.complete ? '收' : '暫收']]) {
      const item = document.createElement('span'); item.textContent = label;
      const value = document.createElement('b'); value.textContent = fmt(bar[key]);
      item.appendChild(value); readout.appendChild(item);
    }
  }
  function draw() {
    box.replaceChildren();
    const bars = D.bars.slice(-days);
    if (!bars.length) return;
    const width = Math.max(box.clientWidth, 260), mobile = width < 560;
    const height = mobile ? 370 : 420;
    const margin = {left: mobile ? 49 : 66, right: mobile ? 82 : 170, top: 24, bottom: 34};
    const right = width - margin.right, bottom = height - margin.bottom;
    const zones = D.zones.filter(z => visible[z.side]);
    const values = bars.flatMap(b => [b.low, b.high]).concat([D.price], zones.flatMap(z => [z.low, z.high]));
    const min = Math.min(...values), max = Math.max(...values), pad = Math.max(max - min, D.price * .02) * .12;
    const lo = Math.max(0, min - pad), hi = max + pad;
    const step = (right - margin.left) / bars.length;
    const x = i => margin.left + (i + .5) * step;
    const y = value => margin.top + (hi - value) / (hi - lo) * (bottom - margin.top);
    const svg = el('svg', {viewBox: `0 0 ${width} ${height}`, height, tabindex: 0, role: 'img',
      'aria-label': `最近 ${bars.length} 個交易日股價與本次支持阻力區間。使用左右方向鍵查看價格。`}, box);
    const title = el('title', {}, svg); title.textContent = '股價與本次支持阻力區間';
    ticks(lo, hi).forEach(value => {
      el('line', {x1: margin.left, x2: right, y1: y(value), y2: y(value), stroke: '#e6e9ef'}, svg);
      text(svg, margin.left - 7, y(value) + 4, value.toLocaleString('en-US', {maximumFractionDigits: 2}), {'text-anchor': 'end', 'font-size': mobile ? 9 : 11});
    });
    text(svg, margin.left, 12, D.unit, {'font-size': 10});
    for (const zone of zones) {
      const group = el('g', {'data-zone': zone.short, 'data-side': zone.side}, svg);
      el('rect', {x: margin.left, y: y(zone.high) - (zone.high === zone.low ? 1 : 0), width: right - margin.left,
        height: Math.max(y(zone.low) - y(zone.high), 2), fill: colors[zone.side], opacity: .10}, group);
      el('line', {x1: margin.left, x2: right, y1: y(zone.center), y2: y(zone.center), stroke: colors[zone.side],
        'stroke-width': 1, 'stroke-dasharray': '3 4', opacity: .65}, group);
    }
    // The historical close line remains legible when many small daily bars are shown.
    el('polyline', {points: bars.map((b, i) => `${x(i)},${y(b.close)}`).join(' '), fill: 'none', stroke: '#7d8da7',
      opacity: .55, 'stroke-width': 1.3, 'stroke-linejoin': 'round'}, svg);
    bars.forEach((bar, i) => {
      const color = bar.close >= bar.open ? '#2a78d6' : '#69768b';
      const group = el('g', {'data-date': bar.date, opacity: bar.complete ? 1 : .35}, svg);
      el('line', {x1: x(i), x2: x(i), y1: y(bar.high), y2: y(bar.low), stroke: color, 'stroke-width': 1}, group);
      const w = Math.max(1, Math.min(step * .58, 8));
      el('rect', {x: x(i) - w / 2, y: Math.min(y(bar.open), y(bar.close)), width: w,
        height: Math.max(Math.abs(y(bar.open) - y(bar.close)), 1.2), fill: color}, group);
    });
    el('line', {x1: margin.left, x2: right, y1: y(D.price), y2: y(D.price), stroke: '#131c47', 'stroke-width': 1.4, 'stroke-dasharray': '6 4'}, svg);
    const labels = zones.map(zone => ({value: zone.center, label: zone.short,
      detail: zone.low === zone.high ? fmt(zone.low) : `${fmt(zone.low)}–${fmt(zone.high)}`, color: colors[zone.side]}));
    labels.push({value: D.price, label: '參考價', detail: fmt(D.price), color: '#131c47'});
    labels.sort((a, b) => b.value - a.value);
    const spacing = mobile ? 32 : 34;
    labels.forEach((item, i) => {item.yy = Math.max(y(item.value), margin.top + 7, i ? labels[i - 1].yy + spacing : 0);});
    if (labels.length && labels[labels.length - 1].yy > bottom - 12) {
      labels[labels.length - 1].yy = bottom - 12;
      for (let i = labels.length - 2; i >= 0; i--) labels[i].yy = Math.min(labels[i].yy, labels[i + 1].yy - spacing);
    }
    for (const item of labels) {
      el('polyline', {points: `${right},${y(item.value)} ${right + 8},${item.yy} ${right + 12},${item.yy}`,
        fill: 'none', stroke: item.color, opacity: .65}, svg);
      text(svg, right + 16, item.yy - 2, item.label + (mobile && item.label !== '參考價' ? ' 中心' : ''), {fill: item.color, 'font-size': mobile ? 10 : 11, 'font-weight': 650});
      text(svg, right + 16, item.yy + 11, mobile ? fmt(item.value) : item.detail, {fill: item.color, 'font-size': mobile ? 9 : 11});
    }
    const axisIndices = [...new Set([0, Math.floor((bars.length - 1) / 2), bars.length - 1])];
    axisIndices.forEach((i, pos) => text(svg, x(i), height - 11, mobile ? bars[i].date.slice(5) : bars[i].date,
      {'text-anchor': pos === 0 ? 'start' : pos === axisIndices.length - 1 ? 'end' : 'middle', 'font-size': mobile ? 9 : 10}));
    const cross = el('line', {y1: margin.top, y2: bottom, stroke: '#7d8da7', 'stroke-dasharray': '3 3', visibility: 'hidden'}, svg);
    const dot = el('circle', {r: 4, fill: '#131c47', stroke: '#fff', 'stroke-width': 2, visibility: 'hidden'}, svg);
    let selected = bars.length - 1;
    function select(index) {
      selected = Math.max(0, Math.min(bars.length - 1, index));
      updateReadout(bars[selected]);
      cross.setAttribute('x1', x(selected)); cross.setAttribute('x2', x(selected)); cross.setAttribute('visibility', 'visible');
      dot.setAttribute('cx', x(selected)); dot.setAttribute('cy', y(bars[selected].close)); dot.setAttribute('visibility', 'visible');
    }
    const hit = el('rect', {x: margin.left, y: margin.top, width: right - margin.left, height: bottom - margin.top,
      fill: 'transparent', 'data-chart-hit': 'true'}, svg);
    function pointer(ev) {
      const bounds = svg.getBoundingClientRect();
      const px = (ev.clientX - bounds.left) * width / bounds.width;
      select(Math.floor((px - margin.left) / step));
    }
    hit.addEventListener('pointermove', pointer);
    hit.addEventListener('pointerdown', pointer);
    hit.addEventListener('pointerleave', ev => {
      if (ev.pointerType === 'touch') return;
      cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); updateReadout(bars[bars.length - 1]);
    });
    svg.addEventListener('keydown', ev => {
      if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') {ev.preventDefault(); select(selected + (ev.key === 'ArrowLeft' ? -1 : 1));}
      if (ev.key === 'Home' || ev.key === 'End') {ev.preventDefault(); select(ev.key === 'Home' ? 0 : bars.length - 1);}
    });
    updateReadout(bars[bars.length - 1]);
    periods.querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.days) === days)));
  }
  const choices = [...new Set([30, 60, 120, 200].filter(n => n <= D.bars.length).concat([days]))].sort((a, b) => a - b);
  for (const count of choices) {
    const button = document.createElement('button'); button.type = 'button'; button.dataset.days = count;
    button.textContent = `${count} 日`; button.addEventListener('click', () => {days = count; draw();}); periods.appendChild(button);
  }
  document.querySelectorAll('[data-layer]').forEach(input => {
    if (!D.zones.some(z => z.side === input.dataset.layer)) {input.parentElement.hidden = true; return;}
    input.addEventListener('change', () => {visible[input.dataset.layer] = input.checked; draw();});
  });
  document.getElementById('print').addEventListener('click', () => window.print());
  let printDetails = [];
  window.addEventListener('beforeprint', () => {
    printDetails = [...document.querySelectorAll('details')].map(node => [node, node.open]);
    printDetails.forEach(([node]) => {node.open = true;});
  });
  window.addEventListener('afterprint', () => printDetails.forEach(([node, open]) => {node.open = open;}));
  let resizeFrame;
  const resize = () => {cancelAnimationFrame(resizeFrame); resizeFrame = requestAnimationFrame(draw);};
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(resize).observe(box);
  else window.addEventListener('resize', resize);
  draw();
})();
