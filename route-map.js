// <bod-route-map data-routes='[...]' data-selected="LYS"> — carte des routes au départ de BOD (d3-geo + Natural Earth).
(function () {
  if (customElements.get('bod-route-map')) return;
  const WORLD = 'https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json';
  const BOD = [-0.715, 44.828];
  let worldP = null;
  const ready = () => new Promise(r => { const t = () => (window.d3 && window.topojson ? r() : setTimeout(t, 60)); t(); });
  const CSS = `
    :host{display:block;width:100%;height:100%;position:relative;font-family:'Geist',system-ui,sans-serif}
    svg{display:block;width:100%;height:100%}
    .land{fill:#E9E5DD;stroke:#FCFBF8;stroke-width:.7}
    .grat{fill:none;stroke:#141824;stroke-opacity:.05}
    .arc{fill:none;stroke-linecap:round;transition:opacity .3s}
    .flow{fill:none;stroke:#fff;stroke-opacity:.9;stroke-dasharray:1.5 10;stroke-linecap:round;animation:flow 1.8s linear infinite;pointer-events:none;transition:opacity .3s}
    @keyframes flow{to{stroke-dashoffset:-23}}
    .dot{cursor:pointer;transition:r .25s,opacity .3s;stroke:#fff;stroke-width:1.5}
    .lbl{font-size:12px;font-weight:500;fill:#141824;paint-order:stroke;stroke:#FCFBF8;stroke-width:4px;stroke-linejoin:round;pointer-events:none;transition:opacity .3s}
    .bod{fill:#141824}
    .ring{fill:none;stroke:oklch(0.52 0.15 255);stroke-width:1.5;transform-box:fill-box;transform-origin:center;animation:ring 2.4s ease-out infinite}
    @keyframes ring{from{transform:scale(.6);opacity:.9}to{transform:scale(3.2);opacity:0}}
    .tip{position:absolute;pointer-events:none;background:#141824;color:#F5F3EE;font-size:13px;line-height:1.35;padding:8px 11px;border-radius:10px;opacity:0;transform:translateY(4px);transition:opacity .15s,transform .15s;white-space:nowrap}
    .tip b{font-weight:600;display:block}
    .tip.on{opacity:1;transform:none}
    .dim .arc,.dim .flow,.dim .dot,.dim .lbl{opacity:.15}
    .dim .hl{opacity:1!important}
    @media (prefers-reduced-motion: reduce){.flow,.ring{animation:none}}
  `;
  class BodRouteMap extends HTMLElement {
    static get observedAttributes() { return ['data-routes', 'data-selected']; }
    constructor() {
      super();
      this.attachShadow({ mode: 'open' }).innerHTML = `<style>${CSS}</style><svg></svg><div class="tip"></div>`;
      this._routes = [];
    }
    connectedCallback() {
      this.style.display = 'block'; this.style.width = '100%'; this.style.height = '100%';
      this._ro = new ResizeObserver(() => this._draw());
      this._ro.observe(this);
      ready().then(() => (worldP = worldP || fetch(WORLD).then(r => r.json()))).then(w => {
        this._land = topojson.feature(w, w.objects.countries);
        this._draw();
      });
    }
    disconnectedCallback() { this._ro && this._ro.disconnect(); }
    attributeChangedCallback(n) {
      if (n === 'data-routes') {
        try { this._routes = JSON.parse(this.getAttribute('data-routes') || '[]'); } catch (e) { this._routes = []; }
        this._draw();
      } else this._hl(this.getAttribute('data-selected'));
    }
    _hl(iata) {
      const g = this.shadowRoot.querySelector('svg > g.routes');
      if (!g) return;
      g.classList.toggle('dim', Boolean(iata));
      g.querySelectorAll('[data-iata]').forEach(el => el.classList.toggle('hl', el.dataset.iata === iata));
    }
    _draw() {
      if (!this._land || !window.d3) return;
      const w = this.clientWidth, h = this.clientHeight;
      if (!w || !h) return;
      const routes = this._routes.filter(r => !r.far && Number.isFinite(r.lat));
      const svg = d3.select(this.shadowRoot.querySelector('svg')).attr('viewBox', `0 0 ${w} ${h}`);
      svg.selectAll('*').remove();
      const pad = Math.min(60, w * 0.06);
      const proj = d3.geoMercator().fitExtent([[pad, pad], [w - pad, h - pad]], { type: 'MultiPoint', coordinates: [BOD, ...routes.map(r => [r.lon, r.lat])] });
      const path = d3.geoPath(proj);
      svg.append('path').attr('class', 'grat').attr('d', path(d3.geoGraticule().step([10, 10])()));
      svg.append('g').selectAll('path').data(this._land.features).join('path').attr('class', 'land').attr('d', path);
      const [x0, y0] = proj(BOD);
      const g = svg.append('g').attr('class', 'routes');
      const tip = this.shadowRoot.querySelector('.tip');
      const sorted = routes.slice().sort((a, b) => a.n - b.n);
      sorted.forEach(r => {
        const [x1, y1] = proj([r.lon, r.lat]);
        const dx = x1 - x0, dy = y1 - y0, dist = Math.hypot(dx, dy) || 1;
        let nx = -dy / dist, ny = dx / dist;
        if (ny > 0) { nx = -nx; ny = -ny; }
        const k = dist * 0.18;
        const d = `M${x0},${y0} Q${(x0 + x1) / 2 + nx * k},${(y0 + y1) / 2 + ny * k} ${x1},${y1}`;
        const sw = 1.2 + Math.min(r.n, 14) * 0.32;
        g.append('path').attr('class', 'arc').attr('data-iata', r.iata).attr('d', d).attr('stroke', r.color).attr('stroke-width', sw).attr('stroke-opacity', 0.55);
        g.append('path').attr('class', 'flow').attr('data-iata', r.iata).attr('d', d).attr('stroke-width', Math.max(1, sw * 0.55));
      });
      sorted.forEach(r => {
        const [x1, y1] = proj([r.lon, r.lat]);
        const rad = 3.5 + Math.min(r.n, 14) * 0.35;
        const dot = g.append('circle').attr('class', 'dot').attr('data-iata', r.iata).attr('cx', x1).attr('cy', y1).attr('r', rad).attr('fill', r.color);
        if (r.n >= 3 || routes.length < 12) {
          g.append('text').attr('class', 'lbl').attr('data-iata', r.iata).attr('x', x1 + rad + 5).attr('y', y1 + 4).text(r.name);
        }
        dot.on('mouseenter', e => {
          dot.attr('r', rad + 3);
          tip.innerHTML = `<b>${r.name}</b>${r.n} départ${r.n > 1 ? 's' : ''} · risque moyen ${r.avg} %`;
          tip.classList.add('on');
          this._hl(r.iata);
        }).on('mousemove', e => {
          const b = this.getBoundingClientRect();
          const x = Math.min(e.clientX - b.left + 14, w - 220);
          tip.style.left = x + 'px'; tip.style.top = (e.clientY - b.top - 46) + 'px';
        }).on('mouseleave', () => {
          dot.attr('r', rad); tip.classList.remove('on');
          this._hl(this.getAttribute('data-selected'));
        }).on('click', () => {
          this.dispatchEvent(new CustomEvent('routeselect', { detail: { iata: r.iata }, bubbles: true, composed: true }));
        });
      });
      g.append('circle').attr('class', 'ring').attr('cx', x0).attr('cy', y0).attr('r', 7);
      g.append('circle').attr('class', 'bod').attr('cx', x0).attr('cy', y0).attr('r', 5.5);
      g.append('text').attr('class', 'lbl').attr('x', x0 - 12).attr('y', y0 + 4).attr('text-anchor', 'end').style('font-weight', 600).text('Bordeaux');
      this._hl(this.getAttribute('data-selected'));
    }
  }
  customElements.define('bod-route-map', BodRouteMap);
})();
