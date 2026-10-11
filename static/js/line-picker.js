/* Scan-or-type product picker for purchase screens.
 *
 *   x-data="{ ...lpSearch({ url: '/catalog/api/variants/', onPick(v) { ... } }), rows: [] }"
 *
 * Adds: query, results, hi (highlighted row), busy, searched, miss (nothing found for a scanned code) and the methods
 * search() / enter() / move(+1|-1) / pick(v) / clear().
 *   - typing (debounce it in the template) lists matching products - name, SKU or barcode
 *   - a barcode scanner "types" the code and presses Enter: Enter asks the server for an EXACT barcode / SKU hit first and
 *     adds that product straight away, without anyone touching the list
 *   - arrow keys + Enter choose from the list; Escape closes it
 * Only stock-tracked ("standard") products can be bought, so services, combos and digital items never show up.
 * Plain script (not deferred) so Alpine can find lpSearch when the page's components start.
 */
(function () {
  function lpSearch(opts) {
    opts = opts || {};
    var seq = 0;
    function get(url, params) {
      url = typeof url === 'function' ? url() : url;      // a function lets the page add things like the chosen warehouse
      var u = url + (url.indexOf('?') < 0 ? '?' : '&') + params;
      return fetch(u, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(function (d) { return (d.results || []).filter(function (v) { return v.tracked !== false; }); });
    }
    return {
      query: '', results: [], hi: 0, busy: false, searched: false, miss: '', failed: false, _moved: false,
      search: function () {
        var q = (this.query || '').trim(), mine = ++seq, self = this;
        this.miss = ''; this.failed = false;
        if (!q) { this.results = []; this.searched = false; this.busy = false; return Promise.resolve(); }
        this.busy = true;
        return get(opts.url, 'q=' + encodeURIComponent(q)).then(function (list) {
          if (mine !== seq) return;                       // a newer keystroke already replaced this answer
          self.results = list; self.hi = 0; self.searched = true; self.busy = false;
        }).catch(function () {
          if (mine !== seq) return;
          self.results = []; self.searched = true; self.busy = false; self.failed = true;
        });
      },
      enter: function () {
        var q = (this.query || '').trim(), self = this;
        if (!q) return Promise.resolve();
        // the user walked down the list with the arrow keys: take that row
        if (this._moved && this.results[this.hi]) return Promise.resolve(this.pick(this.results[this.hi]));
        ++seq; this.busy = true; this.miss = ''; this.failed = false;
        // a scanner: exact barcode / SKU first, whatever the list currently shows
        return get(opts.url, 'exact=1&q=' + encodeURIComponent(q)).then(function (hits) {
          if (hits.length) { self.busy = false; return self.pick(hits[0]); }
          return self.search().then(function () {        // typed text: the best match (the highlighted row)
            if (self.results.length) return self.pick(self.results[self.hi]);
            self.miss = q;
          });
        }).catch(function () { self.busy = false; self.failed = true; });
      },
      move: function (d) {
        if (!this.results.length) return;
        this._moved = true;
        this.hi = (this.hi + d + this.results.length) % this.results.length;
      },
      pick: function (v) {
        if (opts.onPick) opts.onPick.call(this, v);
        this.clear();
      },
      clear: function () { ++seq; this.query = ''; this.results = []; this.hi = 0; this.searched = false; this.busy = false; this.miss = ''; this._moved = false; }
    };
  }
  window.lpSearch = lpSearch;
})();
