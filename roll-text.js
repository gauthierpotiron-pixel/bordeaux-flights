// <roll-text value="12:40"> — chaque caractère qui change roule verticalement.
(function () {
  if (customElements.get('roll-text')) return;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const EASE = 'cubic-bezier(.2,.8,.2,1)';
  class RollText extends HTMLElement {
    static get observedAttributes() { return ['value']; }
    connectedCallback() {
      this.style.display = 'inline-flex';
      this.style.whiteSpace = 'pre';
      this.style.fontVariantNumeric = 'tabular-nums';
      this.setAttribute('aria-label', this.getAttribute('value') || '');
      if (this._v === undefined) { this._v = ''; this._set(this.getAttribute('value') || '', Number(this.getAttribute('delay') || 0)); }
    }
    attributeChangedCallback() { if (this._v !== undefined) this._set(this.getAttribute('value') || '', 0); }
    _set(v, base) {
      if (v === this._v) return;
      this._v = v;
      this.setAttribute('aria-label', v);
      const cells = [...this.children];
      while (cells.length < v.length) {
        const c = document.createElement('span');
        c.setAttribute('aria-hidden', 'true');
        c.style.cssText = 'display:inline-block;position:relative;overflow:hidden;height:1.15em;line-height:1.15em;vertical-align:top';
        const s = document.createElement('span');
        s.style.display = 'block'; s.textContent = '\u00a0';
        c.appendChild(s); this.appendChild(c); cells.push(c);
      }
      while (cells.length > v.length) cells.pop().remove();
      cells.forEach((c, i) => {
        const ch = v[i] === ' ' ? '\u00a0' : v[i];
        while (c.children.length > 1) c.firstChild.remove();
        const cur = c.lastChild;
        cur.style.position = ''; cur.getAnimations && cur.getAnimations().forEach(a => a.finish());
        if (cur.textContent === ch) return;
        if (reduce || !cur.animate) { cur.textContent = ch; return; }
        const n = document.createElement('span');
        n.style.cssText = 'display:block;position:absolute;left:0;top:0;width:100%;text-align:center';
        n.textContent = ch;
        c.appendChild(n);
        const delay = base + i * 35;
        const o = { duration: 520, delay, easing: EASE, fill: 'both' };
        cur.animate([{ transform: 'translateY(0)', opacity: 1, filter: 'blur(0)' }, { transform: 'translateY(-90%)', opacity: 0, filter: 'blur(2px)' }], o);
        const a = n.animate([{ transform: 'translateY(90%)', opacity: 0, filter: 'blur(2px)' }, { transform: 'translateY(0)', opacity: 1, filter: 'blur(0)' }], o);
        a.onfinish = () => { if (n.parentNode !== c) return; cur.remove(); n.style.position = ''; n.getAnimations().forEach(x => x.cancel()); };
      });
    }
  }
  customElements.define('roll-text', RollText);
})();
