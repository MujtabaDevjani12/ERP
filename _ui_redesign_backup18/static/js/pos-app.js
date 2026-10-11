/* ==========================================================================
   POS terminal logic (Alpine component `posApp`). Config comes from window.POS_CONFIG (see dashboard/pos.html).

   Rules this file enforces on the client (the server enforces them again - nothing here is trusted):
   - The cart lives on the SERVER (one per register session / counter). Every change is saved after 400 ms, and a copy is
     kept in localStorage as a backup. The backup holds the cart only - never any payment data.
   - Totals always come from the server's /pos/quote/ so the screen shows exactly what the sale is saved with.
   - Out-of-stock products are never listed (the lookup is called with pos=1) and are refused again at checkout.
   - Every checkout carries a random token, so a double click or a retry after a network error can never create two sales.
   ========================================================================== */
(function () {
  'use strict';

  var CFG = window.POS_CONFIG || {};
  var URLS = CFG.urls || {};
  var BACKUP_KEY = 'pos.cart.' + (CFG.session ? CFG.session.id : '0');
  var LAST_SALE_KEY = 'pos.lastSale.' + (CFG.session ? CFG.session.id : '0');
  var STOCK_CHANGED = 'Stock changed. Please review your cart.';

  function csrf() {
    var m = document.cookie.match('(^|;)\\s*csrftoken\\s*=\\s*([^;]+)');
    if (m) return m.pop();
    var meta = document.querySelector('meta[name=csrf-token]');
    return meta ? meta.getAttribute('content') : '';
  }
  function lsGet(key) { try { return JSON.parse(window.localStorage.getItem(key) || 'null'); } catch (e) { return null; } }
  function lsSet(key, value) { try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage blocked: server copy still works */ } }
  function lsDel(key) { try { window.localStorage.removeItem(key); } catch (e) { /* ignore */ } }
  function uuid() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return 'x' + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
  }
  function round2(n) { return Math.round((n + Number.EPSILON) * 100) / 100; }

  function json(url, body, opts) {
    opts = opts || {};
    return fetch(url, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', keepalive: !!opts.keepalive,
      headers: Object.assign({ 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': csrf() },
        body === undefined || body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      body: body === undefined ? undefined : (body instanceof FormData ? body : JSON.stringify(body)),
    }).then(function (res) {
      return res.json().catch(function () { return { ok: false, error: 'Unexpected response from the server (' + res.status + ').' }; })
        .then(function (data) { data._status = res.status; return data; });
    });
  }

  window.posApp = function () {
    return {
      cfg: CFG, methods: JSON.parse(document.getElementById('pos-methods').textContent || '[]'),
      // catalogue
      query: '', category: null, results: [], loading: true, loadError: false, searchSeq: 0, lastAdded: '', quick: { item: null, siblings: [], timer: null, hold: false, pinned: false, added: true }, hot: 0, resultsFor: null, scanMsg: null, tiles: [], picker: { name: '', image: null, list: [], loading: false, error: '', productId: null }, pendingAdd: null, pendingTimer: null, custAlert: false, scanMsgTimer: null, burst: 0, lastKeyAt: 0, scanTimer: null, scanTimer2: null, prevLen: 0,
      // cart
      moreOpen: false, cart: [], notices: [], customer: null, customerQuery: '', customerResults: [], showCustomers: false,
      shipping: '0', shippingDraft: '', orderDiscount: { type: 'amount', value: '0' }, discountDraft: { type: 'amount', value: '0' }, saleNote: '', staffNote: '',
      billerId: CFG.billerId, totals: { subtotal: 0, discount: 0, tax: 0, shipping: 0, rounding: 0, total: 0, pending: false },
      suppress: 0, saveTimer: null, quoteTimer: null, quoteSeq: 0, quotePromise: null, syncLabel: '', dirtyUnsaved: false,
      // ui
      exp: { category_id: '', amount: '', paid_to: '', note: '', busy: false, error: '' },
      modal: null, busy: false, clock: '', dark: false, toasts: [], held: [], heldCount: 0, heldLoading: false,
      payLines: [], fb: { rating: 0, hover: 0, comment: '', busy: false, sent: false, error: '' }, lastSale: null, token: '', tokenHash: '',
      newCustomer: { name: '', phone: '', cnic: '', email: '', address: '', group: '', error: '' }, cameFrom: null, returnQuery: '', returnMode: 'return', exchangeInfo: null, ord: { q: '', status: '', list: [], loading: false, more: false, page: 1, error: '', sale: null, saleLoading: false, saleError: '', mode: 'view', qty: {}, reason: '', method: 'cash', restock: true, busy: false, formError: '' }, ordTimer: null, cam: { stream: null, error: '', last: '', lastAt: 0, timer: null, detector: null, count: 0 },
      closing: { loading: false, summary: null, error: '', counted: '', note: '', confirm: false },
      confirmBox: { open: false, title: '', message: '', label: 'Confirm', tone: 'danger', resolve: null },

      // ------------------------------------------------------------------ startup
      init: function () {
        var self = this;
        this.dark = document.documentElement.getAttribute('data-theme') === 'dark';
        this.lastSale = lsGet(LAST_SALE_KEY);
        if (CFG.showClock) { this.tickClock(); setInterval(function () { self.tickClock(); }, 30000); }
        this.search();
        this.loadHeld();
        this.loadCart().then(function () {
          if (!self.cart.length) self.useDefaultCustomer();
          if (CFG.exchange) {                                // back from an exchange return: note it on the new sale and remind the cashier
            self.exchangeInfo = CFG.exchange;
            if (!self.cart.length && !self.saleNote) self.saleNote = 'Exchange for ' + CFG.exchange.sale + ' (return ' + CFG.exchange.ret + ')';
            self.toast('Return saved. Now add the new items for the exchange.', 'success');
            try { window.history.replaceState(null, '', window.location.pathname); } catch (e) { /* ignore */ }
          }
        });
        this.$watch(function () { return JSON.stringify(self.cartPayload()); }, function () { self.onCartChanged(); });
        document.addEventListener('visibilitychange', function () {
          if (document.visibilityState === 'visible') { self.search(true); self.loadHeld(); }
          else if (self.dirtyUnsaved) self.saveNow(true);
        });
        window.addEventListener('pagehide', function () { if (self.dirtyUnsaved) self.saveNow(true); });
        window.addEventListener('online', function () { if (self.dirtyUnsaved) self.saveNow(); });
      },
      tickClock: function () { this.clock = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); },
      toggleTheme: function () {
        this.dark = !this.dark;
        document.documentElement.setAttribute('data-theme', this.dark ? 'dark' : 'light');
        try { window.localStorage.setItem('ui.theme', this.dark ? 'dark' : 'light'); } catch (e) { /* ignore */ }
      },

      // ------------------------------------------------------------------ formatting helpers
      num: function (v) { var n = parseFloat(v); return isFinite(n) ? n : 0; },
      fmt: function (n) {
        var d = CFG.decimals;
        if (window.appMoney) return window.appMoney(n, CFG.currency, d);
        return CFG.currency + ' ' + this.num(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
      },
      signed: function (n) { return (n > 0 ? '+' : '') + n.toFixed(2); },
      qty: function (v) { return String(Math.round(this.num(v) * 1000) / 1000); },
      // what the tile shows under the price: services / digital items have no stock to count; a combo counts whole bundles
      stockLabel: function (p) {
        if (p.isGroup) return 'Choose variant';
        if (p.item_type === 'service') return 'Service';
        if (p.item_type === 'digital') return 'Digital';
        if (p.item_type === 'combo') return this.qty(p.stock) + ' combos';
        return this.qty(p.stock) + ' ' + (p.unit || '');
      },
      isLowStock: function (p) { return p.tracked !== false && this.num(p.stock) <= 5; },
      get qtyStep() { return CFG.qtyDecimals > 0 ? String(Math.pow(10, -CFG.qtyDecimals)) : '1'; },
      methodOf: function (code) { return this.methods.find(function (m) { return m.code === code; }); },
      isCash: function (code) { return code === 'cash'; },

      toast: function (text, type) {
        var self = this, t = { id: uuid(), text: text, type: type || 'info' };
        this.toasts.push(t);
        setTimeout(function () { self.toasts = self.toasts.filter(function (x) { return x.id !== t.id; }); }, type === 'error' ? 6500 : 3800);
      },
      ask: function (o) {
        var self = this;
        this.confirmBox = { open: true, title: o.title || 'Are you sure?', message: o.message || '', label: o.label || 'Confirm', tone: o.tone || 'danger', resolve: null };
        return new Promise(function (resolve) { self.confirmBox.resolve = resolve; });
      },
      answerConfirm: function (v) { var r = this.confirmBox.resolve; this.confirmBox.open = false; this.confirmBox.resolve = null; if (r) r(v); },

      // ------------------------------------------------------------------ catalogue
      search: function (silent) {
        var self = this, seq = ++this.searchSeq, forQuery = this.query.trim();
        var p = new URLSearchParams({ pos: '1' });
        if (forQuery) p.set('q', forQuery);
        if (this.category) p.set('category', this.category);
        if (!silent) { this.loading = true; }
        this.loadError = false;
        return fetch(URLS.lookup + '?' + p.toString(), { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
          .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
          .then(function (d) { if (seq === self.searchSeq) { self.results = d.results || []; self.tiles = self.group(self.results); self.resultsFor = forQuery; self.hot = 0; } })
          .catch(function () { if (seq === self.searchSeq) { self.loadError = true; if (!silent) self.results = []; } })
          .then(function () { if (seq === self.searchSeq) self.loading = false; });
      },
      // One tile per product: a product that has several variants on sale shows a single tile and asks which variant to add.
      group: function (list) {
        var out = [], seen = {}, self = this;
        (list || []).forEach(function (p) {
          if ((p.variant_count || 1) > 1 && p.product_id) {
            var g = seen[p.product_id];
            if (!g) {
              g = seen[p.product_id] = Object.assign({}, p, { isGroup: true, tileKey: 'g' + p.product_id, name: p.product || p.name, sku: '', priceTo: self.num(p.price), stockSum: 0, matches: 0 });
              out.push(g);
            }
            g.matches++; g.stockSum += self.num(p.stock);
            if (self.num(p.price) < self.num(g.price)) g.price = p.price;
            if (self.num(p.price) > g.priceTo) g.priceTo = self.num(p.price);
            if (!g.image && p.image) g.image = p.image;
          } else out.push(Object.assign({}, p, { tileKey: 'v' + p.id }));
        });
        return out;
      },
      setCategory: function (id) { this.category = id; this.search(); },
      // A scanner "types" the code very fast and (usually) ends with Enter. We never wait for the debounced list search:
      // the code is looked up on its own (exact barcode / SKU) and the product goes straight into the cart.
      onSearchInput: function () {
        var self = this, now = Date.now();
        if (now - (this.lastKeyAt || 0) > 120) this.burst = 0;          // a pause: a new burst starts
        this.burst = (this.burst || 0) + 1; this.lastKeyAt = now;
        clearTimeout(this.scanTimer); clearTimeout(this.scanTimer2);
        var q = this.query.trim(), need = Math.max(3, Math.min(CFG.minScanChars || 4, 6));
        var jumped = q.length - (this.prevLen || 0) >= need;            // whole code arrived in ONE input event (paste / phone scanner)
        this.prevLen = q.length;
        if (q.length < need || !/^\S+$/.test(q)) return;
        // 1) fast burst of keys or a pasted code, then silence: scanner without Enter
        if (this.burst >= need || jumped) {
          this.scanTimer = setTimeout(function () { if (self.query.trim() === q) self.submitQuery(q, true); }, jumped ? 30 : 140);
        }
        // 2) slow scanners / any code left sitting in the box: after a short pause, add it ONLY if it is exactly a barcode or SKU
        this.scanTimer2 = setTimeout(function () { if (self.query.trim() === q) self.submitQuery(q, 'exact'); }, 350);
      },
      handleEnter: function () {
        clearTimeout(this.scanTimer); clearTimeout(this.scanTimer2);
        var q = this.query.trim();
        if (q) this.submitQuery(q, false);
      },
      moveHot: function (d) {
        if (!this.tiles.length) return;
        this.hot = Math.max(0, Math.min(this.tiles.length - 1, (this.hot || 0) + d));
        this.$nextTick(function () { var el = document.querySelector('.pos-tile.is-hot'); if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' }); });
      },
      // Type or scan a barcode, SKU or product name and press Enter (or just scan): the matching product goes into the cart.
      // Exact barcode/SKU wins; otherwise the only match; otherwise (Enter on a name) the highlighted / first match.
      submitQuery: function (q, quiet) {
        var self = this, low = q.toLowerCase(), codeLike = /^\S+$/.test(q);
        // the same code is already being looked up (auto-timer + Enter): never add it twice
        if (this._busyQ === q) return Promise.resolve();
        this._busyQ = q;
        var same = function (r) { return (r.barcode || '').toLowerCase() === low || (r.sku || '').toLowerCase() === low; };
        var get = function (extra) {
          return fetch(URLS.lookup + '?pos=1' + (extra || '') + '&q=' + encodeURIComponent(q), { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).then(function (d) { return d.results || []; });
        };
        var done = function (p) {
          if (p.isGroup) { if (self.openVariants(p) === false) return; self.query = ''; self.prevLen = 0; self.burst = 0; self.search(true); return; }
          if (self.addToCart(p, true) === false) return;                  // a customer is needed first: the code stays in the box
          self.query = ''; self.prevLen = 0; self.burst = 0; clearTimeout(self.scanTimer); clearTimeout(self.scanTimer2); self.search(true); self.focusSearch();
          self.setScanMsg('ok', p.name + ' added', q);
        };
        var pick = function (list, usedFilter) {
          var hit = list.find(same);
          if (hit) return hit;
          if (quiet === 'exact') return null;
          if (list.length === 1 && (!quiet || codeLike)) return list[0];
          if (list.length > 1 && !quiet) { var ts = self.group(list); return ts[Math.min(self.hot || 0, ts.length - 1)]; }
          return null;
        };
        var miss = function () {
          if (quiet) return;
          return fetch(URLS.lookup + '?exact=1&q=' + encodeURIComponent(q), { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
            var any = d.results || [];
            self.beep(false);
            if (any.length) { self.setScanMsg('warn', any[0].name + ' is out of stock', q); self.toast('“' + any[0].name + '” is out of stock.', 'warning'); }
            else { self.setScanMsg('bad', 'No product found for ' + q, q); self.toast('No product found for “' + q + '”.', 'error'); if (self.$refs.searchInput) self.$refs.searchInput.select(); }
          });
        };
        // the list on screen already answers this query (no waiting for the network)
        if (this.resultsFor === q && !this.category && !this.loading) {
          var now = pick(this.results);
          if (now) { done(now); this._busyQ = null; return Promise.resolve(); }
        }
        var wasCategory = this.category;
        var release = function () { if (self._busyQ === q) self._busyQ = null; };
        return get('').then(function (list) {            // search the whole catalogue, whatever category tab is open
          var hit = pick(list);
          if (hit) { done(hit); return; }
          return miss();
        }).catch(function () { if (!quiet) self.setScanMsg('bad', 'Could not look this up - check the connection', q); }).then(release);
      },
      setScanMsg: function (tone, text, code) {
        var self = this; this.scanMsg = { tone: tone, text: text, code: code };
        clearTimeout(this.scanMsgTimer);
        this.scanMsgTimer = setTimeout(function () { self.scanMsg = null; }, tone === 'ok' ? 3500 : 9000);
      },
      hue: function (name) { var h = 0, i, str = String(name || ''); for (i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) % 360; return h; },
      // ---- product quick view: shown whenever a product is added (scan, Enter or tile click). Never blocks the next scan.
      showQuick: function (p, added) {
        try {
          var q = this.quick, same = q.item && p.product_id && q.item.product_id === p.product_id;
          q.item = p; q.added = added !== false;
          if (!same) {
            q.siblings = [];
            if (p.product_id) {
              fetch(URLS.lookup + '?pos=1&product=' + p.product_id, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                .then(function (r) { return r.json(); })
                .then(function (d) { if (q.item && q.item.product_id === p.product_id && (d.results || []).length > 1) q.siblings = d.results; })
                .catch(function () { /* the popup still works without the variant list */ });
            }
          }
          this.armQuick();
        } catch (e) { if (window.console) console.error('Product popup failed', e); }
      },
      // the (i) button on a cart line: look the product up again and show its details without adding anything
      detailsFor: function (it) {
        var self = this;
        fetch(URLS.lookup + '?exact=1&q=' + encodeURIComponent(it.sku || ''), { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            var hit = (d.results || []).find(function (r) { return r.id === it.variant_id; }) || (d.results || [])[0];
            if (hit) self.showQuick(hit, false); else self.toast('Details are not available for this item.', 'info');
          }).catch(function () { self.toast('Could not load the product details.', 'error'); });
      },
      armQuick: function () {
        var q = this.quick;
        clearTimeout(q.timer);
        if (!q.hold && !q.pinned) q.timer = setTimeout(function () { q.item = null; q.siblings = []; }, 12000);
      },
      closeQuick: function () { clearTimeout(this.quick.timer); this.quick.item = null; this.quick.siblings = []; this.quick.hold = false; this.quick.pinned = false; },
      quickLine: function () { var q = this.quick.item; return q ? this.cart.find(function (i) { return i.variant_id === q.id; }) || null : null; },
      quickStep: function (d) {
        var it = this.quickLine();
        if (!it) { if (d > 0 && this.quick.item) this.addToCart(this.quick.item); return; }
        this.changeQty(it, d);
        if (!this.quickLine()) this.closeQuick(); else this.armQuick();
      },
      beep: function (ok) {
        try {
          var AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
          this.audio = this.audio || new AC();
          var o = this.audio.createOscillator(), g = this.audio.createGain();
          o.type = 'sine'; o.frequency.value = ok ? 1250 : 240; g.gain.value = 0.06;
          o.connect(g); g.connect(this.audio.destination); o.start(); o.stop(this.audio.currentTime + (ok ? 0.07 : 0.22));
        } catch (e) { /* sound is a nicety */ }
      },
      flash: function (key) {
        var self = this; this.lastAdded = key;
        this.$nextTick(function () {
          var el = document.querySelector('.pos-line[data-key="' + key + '"]');
          if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        });
        clearTimeout(this.flashTimer);
        this.flashTimer = setTimeout(function () { self.lastAdded = ''; }, 1100);
      },

      // ------------------------------------------------------------------ cart
      cartPayload: function () {
        return {
          items: this.cart.map(function (i) {
            return { variant_id: i.variant_id, name: i.name, sku: i.sku, unit: i.unit, image: i.image, unit_price: String(i.unit_price),
              list_price: String(i.list_price), discount_amount: String(i.discount_amount || 0), quantity: String(i.quantity), stock: String(i.stock) };
          }),
          customer: this.customer ? { id: this.customer.id, name: this.customer.name, phone: this.customer.phone || '' } : null,
          order_discount: { type: this.orderDiscount.type, value: String(this.orderDiscount.value || 0) },
          shipping: String(this.shipping || 0),
          sale_note: this.saleNote, staff_note: this.staffNote,
        };
      },
      applyCart: function (c) {
        this.suppress++;
        var self = this;
        this.cart = (c.items || []).map(function (i) {
          return { key: 'v' + i.variant_id, variant_id: i.variant_id, name: i.name, sku: i.sku || '', unit: i.unit || '', image: i.image || null,
            unit_price: self.num(i.unit_price), list_price: self.num(i.list_price || i.unit_price), discount_amount: self.num(i.discount_amount),
            quantity: self.num(i.quantity), stock: self.num(i.stock) };
        });
        this.customer = c.customer || null;
        this.customerQuery = this.customer ? this.customer.name : '';
        var od = c.order_discount || {};
        this.orderDiscount = { type: od.type === 'percent' ? 'percent' : 'amount', value: String(od.value || 0) };
        this.saleNote = c.sale_note || '';
        this.staffNote = c.staff_note || '';
        this.shipping = String(c.shipping || 0);
        var self2 = this;
        setTimeout(function () { self2.suppress = Math.max(0, self2.suppress - 1); self2.refreshQuote(); }, 60);
      },
      loadCart: function () {
        var self = this;
        return json(URLS.cartGet).then(function (d) {
          if (d.ok && d.cart && (d.cart.items.length || d.cart.customer)) {
            self.applyCart(d.cart);
            if (d.notices && d.notices.length) self.notices = d.notices;
            lsSet(BACKUP_KEY, { savedAt: Date.now(), synced: true, cart: self.cartPayload() });
            return;
          }
          // nothing on the server: only restore the device copy if the server never got it (e.g. connection dropped mid-save)
          var b = lsGet(BACKUP_KEY);
          if (b && b.synced === false && b.cart && b.cart.items && b.cart.items.length) {
            self.applyCart(b.cart);
            self.toast('Your previous cart was restored.', 'info');
            self.dirtyUnsaved = true; self.saveNow();
          } else if (b) { lsDel(BACKUP_KEY); }
        }).catch(function () {
          var b = lsGet(BACKUP_KEY);
          if (b && b.cart && b.cart.items && b.cart.items.length) { self.applyCart(b.cart); self.syncLabel = 'Offline · saved on this device'; }
        });
      },
      onCartChanged: function () {
        if (this.suppress) return;
        var self = this;
        this.dirtyUnsaved = true;
        lsSet(BACKUP_KEY, { savedAt: Date.now(), synced: false, cart: this.cartPayload() });
        this.syncLabel = 'Saving…';
        clearTimeout(this.saveTimer);
        this.saveTimer = setTimeout(function () { self.saveNow(); }, 400);
        // instant local estimate, then the server's exact figures
        var sub = 0, disc = 0;
        this.cart.forEach(function (i) { sub += self.num(i.quantity) * self.num(i.unit_price); disc += self.num(i.discount_amount); });
        this.totals = { subtotal: round2(sub), discount: round2(disc), tax: this.totals.tax, total: round2(sub - disc + this.totals.tax), pending: true };
        clearTimeout(this.quoteTimer);
        this.quoteTimer = setTimeout(function () { self.refreshQuote(); }, 250);
      },
      saveNow: function (keepalive) {
        var self = this, payload = this.cartPayload();
        clearTimeout(this.saveTimer);
        return json(URLS.cartSave, { cart: payload }, { keepalive: !!keepalive }).then(function (d) {
          if (d.ok) {
            self.dirtyUnsaved = false; self.syncLabel = 'Saved';
            lsSet(BACKUP_KEY, { savedAt: Date.now(), synced: true, cart: payload });
          } else if (d._status === 409) { self.syncLabel = ''; self.registerGone(); }
          else { self.syncLabel = 'Not saved'; }
        }).catch(function () {
          self.syncLabel = 'Offline · saved on this device';
          clearTimeout(self.saveTimer); self.saveTimer = setTimeout(function () { self.saveNow(); }, 5000);
        });
      },
      refreshQuote: function () {
        var self = this, seq = ++this.quoteSeq;
        clearTimeout(this.quoteTimer); this.quoteTimer = null;
        if (!this.cart.length) { this.totals = { subtotal: 0, discount: 0, tax: 0, shipping: 0, rounding: 0, total: 0, pending: false }; this.quotePromise = null; return Promise.resolve(); }
        var p = json(URLS.quote, {
          items: this.checkoutItems(), order_discount: this.orderDiscount, shipping: this.shipping,
        }).then(function (d) {
          if (seq !== self.quoteSeq) return;
          if (d.ok) { self.totals = { subtotal: self.num(d.subtotal), discount: self.num(d.discount), tax: self.num(d.tax), shipping: self.num(d.shipping), rounding: self.num(d.rounding), total: self.num(d.total), pending: false }; }
          else {
            self.totals.pending = true;
            if (d.code === 'stock_changed') { self.toast(STOCK_CHANGED, 'warning'); self.reloadCartFromServer(); }
            else if (d.error) self.toast(d.error, 'error');
          }
        }).catch(function () { /* offline: keep the estimate, checkout will price it on the server */ });
        this.quotePromise = p;
        return p;
      },
      ensureQuote: function () {
        if (this.quoteTimer) { clearTimeout(this.quoteTimer); this.quoteTimer = null; return this.refreshQuote(); }
        return this.quotePromise || Promise.resolve();
      },
      reloadCartFromServer: function () {
        var self = this;
        return this.saveNow().then(function () { return self.loadCart(); });
      },
      registerGone: function () {
        this.toast('This register is no longer open. Select a counter to continue.', 'error');
        var self = this;
        setTimeout(function () { window.location.href = URLS.counters; }, 1200);
      },

      cartQty: function (id) { var it = this.cart.find(function (i) { return i.variant_id === id; }); return it ? this.num(it.quantity) : 0; },
      atMax: function (p) { return !p.isGroup && !CFG.allowOverSelling && this.cartQty(p.id) + 1 > this.num(p.stock); },
      allowMore: function (it) { return CFG.allowOverSelling || this.num(it.quantity) + 1 <= this.num(it.stock); },
      // ---- a customer must be chosen before the first item goes into the cart (Settings > POS behaviour can switch this off)
      customerBlocked: function (pending) {
        if (!CFG.requireCustomer || this.customer) return false;
        var self = this;
        if (pending) {
          this.pendingAdd = pending; clearTimeout(this.pendingTimer);
          this.pendingTimer = setTimeout(function () { self.pendingAdd = null; }, 120000);
        }
        this.beep(false);
        this.custAlert = true; setTimeout(function () { self.custAlert = false; }, 2600);
        if (this.confirmBox.open) return true;
        this.ask({ title: 'Select a customer first', message: pending
            ? 'Please choose a customer (or add a new one) before adding products to the sale. The product you picked is added as soon as you choose.'
            : 'Please choose a customer (or add a new one) before taking payment.', label: 'Select customer', tone: 'primary' })
          .then(function (ok) { if (ok) { self.focusCustomer(); self.searchCustomers(true); } else { self.pendingAdd = null; } });
        return true;
      },
      // the shop's default customer (Settings > Defaults) is used for every new sale
      useDefaultCustomer: function () {
        if (CFG.defaultCustomer && !this.customer) { this.customer = { id: CFG.defaultCustomer.id, name: CFG.defaultCustomer.name, phone: CFG.defaultCustomer.phone || '' }; this.customerQuery = this.customer.name; }
      },
      // a product with several variants: ask which one goes into the cart
      openVariants: function (g) {
        if (this.customerBlocked({ p: g, scanned: false })) return false;
        var self = this, pk = this.picker;
        pk.name = g.product || g.name; pk.image = g.image || null; pk.list = []; pk.loading = true; pk.error = ''; pk.productId = g.product_id;
        this.modal = 'variants';
        fetch(URLS.lookup + '?pos=1&all=1&product=' + g.product_id, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
          .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
          .then(function (d) {
            if (pk.productId !== g.product_id) return;
            pk.list = d.results || []; pk.loading = false;
            if (!pk.list.length) { pk.error = 'No variants of this product are available.'; }
            else if (pk.list.length === 1 && !pk.list[0].out_of_stock) { self.pickVariant(pk.list[0]); }
          })
          .catch(function () { pk.loading = false; pk.error = 'Could not load the variants. Check the connection and try again.'; });
        return true;
      },
      pickVariant: function (v) {
        if (v.out_of_stock) { this.beep(false); this.toast('This variant is out of stock.', 'warning'); return; }
        if (this.addToCart(v) === false) return;
        this.closeModal(false);
      },
      addToCart: function (p, scanned) {
        if (p.isGroup) return this.openVariants(p);
        if (this.customerBlocked({ p: p, scanned: scanned })) return false;
        var it = this.cart.find(function (i) { return i.variant_id === p.id; });
        var stock = this.num(p.stock);
        if (stock <= 0) { this.beep(false); this.toast('Product is currently out of stock.', 'warning'); return false; }
        if (it) {
          if (!this.allowMore(it)) { this.beep(false); this.toast('Only ' + this.qty(it.stock) + ' in stock.', 'warning'); return false; }
          it.quantity = this.num(it.quantity) + 1; it.stock = stock;
        } else {
          this.cart.push({ key: 'v' + p.id, variant_id: p.id, name: p.name, sku: p.sku || '', unit: p.unit || '', image: p.image || null,
            unit_price: this.num(p.price), list_price: this.num(p.price), discount_amount: 0, quantity: 1, stock: stock });
        }
        this.flash('v' + p.id);
        this.showQuick(p, true);
        if (scanned) this.beep(true);
        return true;
      },
      changeQty: function (it, delta) {
        var q = this.num(it.quantity) + delta;
        if (q <= 0) { this.cart = this.cart.filter(function (i) { return i.key !== it.key; }); return; }
        if (delta > 0 && !this.allowMore(it)) { this.toast('Only ' + this.qty(it.stock) + ' in stock.', 'warning'); return; }
        it.quantity = q;
      },
      normalizeQty: function (it) {
        var q = this.num(it.quantity), dec = CFG.qtyDecimals;
        q = dec > 0 ? Math.round(q * Math.pow(10, dec)) / Math.pow(10, dec) : Math.round(q);
        if (q <= 0) { this.cart = this.cart.filter(function (i) { return i.key !== it.key; }); return; }
        if (!CFG.allowOverSelling && q > this.num(it.stock)) { q = this.num(it.stock); this.toast('Only ' + this.qty(it.stock) + ' in stock.', 'warning'); }
        it.quantity = q;
      },
      removeLine: function (idx) { this.cart.splice(idx, 1); },
      lineTotal: function (it) { return Math.max(0, this.num(it.quantity) * this.num(it.unit_price) - this.num(it.discount_amount)); },
      resetSale: function () {
        this.closeQuick();
        this.suppress++;
        this.cart = []; this.customer = null; this.customerQuery = ''; this.customerResults = []; this.pendingAdd = null; this.useDefaultCustomer();
        this.orderDiscount = { type: 'amount', value: '0' }; this.shipping = '0'; this.saleNote = ''; this.staffNote = ''; this.notices = [];
        this.totals = { subtotal: 0, discount: 0, tax: 0, shipping: 0, rounding: 0, total: 0, pending: false };
        this.payLines = []; this.token = ''; this.tokenHash = '';
        var self = this;
        setTimeout(function () { self.suppress = Math.max(0, self.suppress - 1); }, 60);
      },
      clearCart: function () {
        var self = this;
        if (!this.cart.length) return;
        this.ask({ title: 'Clear this cart?', message: 'All items in the current sale will be removed.', label: 'Clear cart' }).then(function (ok) {
          if (!ok) return;
          self.resetSale(); self.dirtyUnsaved = false; lsDel(BACKUP_KEY); self.syncLabel = '';
          json(URLS.cartClear, {}).catch(function () { /* the next save will empty it */ });
          self.toast('Cart cleared.', 'info'); self.focusSearch();
        });
      },
      openShipping: function () {
        this.shippingDraft = this.num(this.shipping) > 0 ? String(this.shipping) : '';
        this.modal = 'shipping';
        this.$nextTick(function () { var el = document.getElementById('ship-amt'); if (el) { el.focus(); el.select(); } });
      },
      applyShipping: function () {
        var v = this.num(this.shippingDraft);
        if (v < 0) { this.toast('The shipping charge cannot be negative.', 'error'); return; }
        this.shipping = String(Math.round(v * 100) / 100);
        this.closeModal();
        this.toast(v > 0 ? 'Shipping charge added.' : 'Shipping charge removed.', 'info');
      },
      applyDiscount: function () {
        var v = this.num(this.discountDraft.value);
        if (v < 0 || (this.discountDraft.type === 'percent' && v > 100)) { this.toast('Enter a valid discount (percent must be 0–100).', 'error'); return; }
        this.orderDiscount = { type: this.discountDraft.type, value: String(v) };
        this.cart.forEach(function (i) { i.discount_amount = Math.max(0, parseFloat(i.discount_amount) || 0); });
        this.closeModal();
        this.toast(v > 0 ? 'Discount applied.' : 'Discount removed.', 'info');
      },

      // ------------------------------------------------------------------ customer
      focusCustomer: function () { var el = this.$refs.customerInput; if (el) { el.focus(); el.select(); } },
      focusSearch: function () { var self = this; this.$nextTick(function () { if (self.$refs.searchInput) self.$refs.searchInput.focus(); }); },
      searchCustomers: function (onFocus) {
        var self = this, q = this.customerQuery.trim();
        if (this.customer && q === this.customer.name) { this.showCustomers = false; return; }
        if (!q && !onFocus) { this.customerResults = []; return; }
        fetch(URLS.customerSearch + '?q=' + encodeURIComponent(q), { credentials: 'same-origin' }).then(function (r) { return r.json(); })
          .then(function (d) { self.customerResults = d.results || []; self.showCustomers = true; })
          .catch(function () { self.customerResults = []; });
      },
      openAddCustomer: function () {
        var self = this;
        this.cameFrom = this.modal;                            // coming from the payment window? go back there afterwards
        this.modal = 'customer';
        this.$nextTick(function () { if (self.$refs.newCustomerName) self.$refs.newCustomerName.focus(); });
      },
      closeCustomerModal: function () {
        var back = this.cameFrom;
        this.cameFrom = null;
        if (back === 'payment') { this.modal = 'payment'; return; }
        this.closeModal();
      },
      selectCustomer: function (c) { this.customer = { id: c.id, name: c.name, phone: c.phone || '' }; this.customerQuery = c.name; this.showCustomers = false; if (!this.modal) this.focusSearch();
        if (this.pendingAdd) { var pa = this.pendingAdd, self = this; this.pendingAdd = null; clearTimeout(this.pendingTimer); this.$nextTick(function () { self.addToCart(pa.p, pa.scanned); }); } },
      clearCustomer: function () { this.customer = null; this.customerQuery = ''; this.customerResults = []; },
      addCustomer: function () {
        var self = this, c = this.newCustomer;
        var f = new FormData();
        ['name', 'phone', 'email', 'cnic', 'address', 'group'].forEach(function (k) { f.append(k, c[k] || ''); });
        this.busy = true; c.error = '';
        json(URLS.customerAdd, f).then(function (d) {
          if (d.id) {
            self.selectCustomer(d);
            self.newCustomer = { name: '', phone: '', cnic: '', email: '', address: '', group: '', error: '' };
            self.closeCustomerModal(); self.toast('Customer added.', 'success');
          } else { c.error = d.error || 'Could not save this customer.'; }
        }).catch(function () { c.error = 'Network error. Please try again.'; }).then(function () { self.busy = false; });
      },

      // ------------------------------------------------------------------ hold / resume
      get heldBadge() { return this.heldCount; },
      loadHeld: function () {
        var self = this;
        this.heldLoading = true;
        return fetch(URLS.held, { credentials: 'same-origin' }).then(function (r) { return r.json(); })
          .then(function (d) { self.held = d.sales || []; self.heldCount = self.held.length; })
          .catch(function () { /* keep the old list */ }).then(function () { self.heldLoading = false; });
      },
      openHeld: function () { this.modal = 'held'; this.loadHeld(); },
      holdSale: function () {
        var self = this;
        if (!this.cart.length || this.busy) return;
        this.busy = true;
        var p = this.cartPayload();
        json(URLS.finalize, {
          checkout_token: 'hold-' + uuid(), hold: true, items: this.checkoutItems(), order_discount: this.orderDiscount, shipping: this.shipping,
          customer_id: this.customer ? this.customer.id : null, sale_note: p.sale_note, staff_note: p.staff_note, biller_id: this.billerId,
        }).then(function (d) {
          if (d.ok) {
            self.resetSale(); self.dirtyUnsaved = false; lsDel(BACKUP_KEY); self.syncLabel = '';
            self.toast('Sale held as ' + d.reference + '. Resume it from Held.', 'success'); self.loadHeld(); self.focusSearch();
          } else self.handleCheckoutError(d);
        }).catch(function () { self.toast('Network error. The sale was not held.', 'error'); }).then(function () { self.busy = false; });
      },
      resumeHeld: function (s) {
        var self = this;
        if (this.cart.length) { this.toast('Hold or clear the current cart before resuming another sale.', 'warning'); return; }
        this.busy = true;
        json(URLS.resume.replace('/0/', '/' + s.id + '/'), {}).then(function (d) {
          if (d.ok) {
            self.applyCart(d.cart);
            self.notices = d.notices || [];
            lsSet(BACKUP_KEY, { savedAt: Date.now(), synced: true, cart: d.cart });
            self.closeModal(); self.toast('Resumed ' + d.reference + '.', 'success'); self.loadHeld();
          } else { self.toast(d.error || 'Could not resume this sale.', 'error'); self.loadHeld(); }
        }).catch(function () { self.toast('Network error. Please try again.', 'error'); }).then(function () { self.busy = false; });
      },

      // ------------------------------------------------------------------ payment + checkout
      checkoutItems: function () {
        var self = this;
        return this.cart.filter(function (i) { return self.num(i.quantity) > 0; })
          .map(function (i) { return { variant_id: i.variant_id, quantity: String(i.quantity), unit_price: String(i.unit_price), discount_amount: String(i.discount_amount || 0) }; });
      },
      get pay() {
        var self = this, total = round2(this.num(this.totals.total)), tendered = 0, nonCash = 0, hasCash = false, missingRef = null, giftShort = false;
        this.payLines.forEach(function (ln) {
          var a = round2(self.num(ln.amount));
          if (a <= 0) return;
          tendered += a;
          if (self.isCash(ln.method)) hasCash = true; else nonCash += a;
          var m = self.methodOf(ln.method);
          if (((m && m.requires_reference) || ln.method === 'gift_card') && !String(ln.reference_no || '').trim()) missingRef = ln.method === 'gift_card' ? 'the gift card number' : m.name;
          if (ln.method === 'gift_card' && ln.gc && ln.gc.ok && a > self.num(ln.gc.balance) + 0.004) giftShort = true;
        });
        tendered = round2(tendered);
        var change = hasCash ? round2(Math.max(0, tendered - total)) : 0, remaining = round2(Math.max(0, total - tendered));
        var out = { total: total, tendered: tendered, change: change, remaining: remaining, tone: 'is-ok', message: '', canSubmit: true };
        if (this.totals.pending) { out.message = 'Calculating the exact total…'; out.tone = 'is-warn'; out.canSubmit = false; }
        else if (total > 0 && tendered <= 0 && !this.customer) { out.message = 'Enter the amount received, or choose a customer above to sell on credit.'; out.tone = 'is-bad'; out.canSubmit = false; }
        else if (missingRef) { out.message = missingRef === 'the gift card number' ? 'Enter the gift card number.' : 'Enter the reference / transaction ID for ' + missingRef + '.'; out.tone = 'is-bad'; out.canSubmit = false; }
        else if (giftShort) { out.message = 'The gift card does not have enough balance for this amount. Lower it and pay the rest another way.'; out.tone = 'is-bad'; out.canSubmit = false; }
        else if (nonCash > total + 0.004) { out.message = 'Card / bank / wallet payments cannot be more than the amount due.'; out.tone = 'is-bad'; out.canSubmit = false; }
        else if (!hasCash && tendered > total + 0.004) { out.message = 'Only cash can be more than the amount due.'; out.tone = 'is-bad'; out.canSubmit = false; }
        else if (remaining > 0.004 && !this.customer) { out.message = 'Part payment: ' + this.fmt(remaining) + ' is still due. Choose the customer above and the balance stays on their account, or enter the full amount.'; out.tone = 'is-bad'; out.canSubmit = false; }
        else if (remaining > 0.004) { out.message = 'Partial payment: ' + this.fmt(remaining) + ' will stay due on the customer’s account.'; out.tone = 'is-warn'; }
        else if (change > 0) { out.message = 'Paid in full. Return ' + this.fmt(change) + ' change to the customer.'; }
        else { out.message = 'Paid in full.'; }
        return out;
      },
      get closingDiff() {
        if (!this.closing.summary) return 0;
        return round2(this.num(this.closing.counted) - this.num(this.closing.summary.expected_cash));
      },
      openPayment: function () {
        var self = this;
        if (!this.cart.length || this.busy || this.modal) return;
        if (this.customerBlocked(null)) return;
        this.busy = true;
        this.ensureQuote().then(function () {
          return json(URLS.stockCheck, { items: self.checkoutItems() }).catch(function () { return { ok: true }; });
        }).then(function (d) {
          if (d && d.ok === false && d.problems && d.problems.length) {
            self.reconcile(d.problems); self.toast(d.message || STOCK_CHANGED, 'warning'); self.search(true); return;
          }
          if (d && d._status === 409) { self.registerGone(); return; }
          self.payLines = [{ method: CFG.defaultMethod, amount: self.num(self.totals.total).toFixed(2), reference_no: '' }];
          self.modal = 'payment';
          self.$nextTick(function () { var el = document.getElementById('pay-amount-0'); if (el) { el.focus(); el.select(); } });
        }).then(function () { self.busy = false; }, function () { self.busy = false; });
      },
      useDeposit: function (ln) {                             // the chosen customer's prepaid deposit card
        if (!this.customer) return;
        this.checkGiftCard(ln, { customer: this.customer.id });
      },
      checkGiftCard: function (ln, byCustomer) {
        var self = this, code = String(ln.reference_no || '').trim();
        if (!byCustomer && !code) return;
        ln.gc = { busy: true };
        fetch(URLS.giftCard + (byCustomer ? '?customer=' + encodeURIComponent(byCustomer.customer) : '?code=' + encodeURIComponent(code)), { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            ln.gc = d;
            if (d.ok) {
              ln.reference_no = d.code;
              var bal = self.num(d.balance), want = self.num(ln.amount);
              if (want > bal) ln.amount = bal.toFixed(2);          // never ask a card for more than it holds; the rest can be split to another method
            }
          })
          .catch(function () { ln.gc = { ok: false, error: 'Could not reach the server to check this card.' }; });
      },
      addPayLine: function () {
        var left = this.pay.remaining;
        var used = this.payLines.map(function (l) { return l.method; });
        var next = (this.methods.find(function (m) { return used.indexOf(m.code) === -1; }) || this.methods[0] || { code: 'cash' }).code;
        this.payLines.push({ method: next, amount: left > 0 ? left.toFixed(2) : '', reference_no: '' });
      },
      partPayment: function (fraction) {                      // "Half now" - the rest stays due on the customer's account
        var line = this.payLines[0];
        if (!line) return;
        line.amount = (Math.round(this.num(this.totals.total) * fraction * 100) / 100).toFixed(2);
        if (!this.customer) { this.toast('Choose the customer who will pay the rest later.', 'info'); this.$nextTick(function () { var el = document.getElementById('pay-customer'); if (el) el.focus(); }); }
      },
      quickCash: function (amount) {
        var line = this.payLines.find(function (l) { return l.method === 'cash'; });
        if (!line) { line = { method: 'cash', amount: '', reference_no: '' }; this.payLines.push(line); }
        line.amount = this.num(amount).toFixed(2);
      },
      tokenFor: function () {
        var hash = JSON.stringify([this.checkoutItems(), this.orderDiscount, this.shipping, this.customer ? this.customer.id : null]);
        if (hash !== this.tokenHash || !this.token) { this.tokenHash = hash; this.token = 'co-' + uuid(); }
        return this.token;
      },
      completeSale: function () {
        var self = this;
        if (this.busy || this.modal !== 'payment' || !this.pay.canSubmit) return;
        this.busy = true;
        var payments = this.payLines.filter(function (l) { return self.num(l.amount) > 0; })
          .map(function (l) { return { method: l.method, amount: String(round2(self.num(l.amount))), reference_no: (l.reference_no || '').trim() }; });
        json(URLS.finalize, {
          checkout_token: this.tokenFor(), items: this.checkoutItems(), order_discount: this.orderDiscount, shipping: this.shipping, payments: payments,
          customer_id: this.customer ? this.customer.id : null, sale_note: this.saleNote, staff_note: this.staffNote, biller_id: this.billerId,
        }).then(function (d) {
          if (d.ok) { self.saleDone(d); } else { self.handleCheckoutError(d); }
        }).catch(function () {
          self.toast('Network error. Your cart is safe — press Complete sale again. The sale will not be duplicated.', 'error');
        }).then(function () { self.busy = false; });
      },
      saleDone: function (d) {
        this.lastSale = d;
        this.fb = { rating: 0, hover: 0, comment: '', busy: false, sent: false, error: '' };
        lsSet(LAST_SALE_KEY, { reference: d.reference, print_url: d.print_url, invoice_url: d.invoice_url, order_url: d.order_url, total: d.total, paid: d.paid,
          change: d.change, due_amount: d.due_amount, customer: d.customer });
        this.resetSale(); this.dirtyUnsaved = false; lsDel(BACKUP_KEY); this.syncLabel = '';
        this.modal = 'success';
        var self = this;
        this.$nextTick(function () { if (self.$refs.newSaleBtn) self.$refs.newSaleBtn.focus(); });
        this.search(true); this.loadHeld();
      },
      handleCheckoutError: function (d) {
        if (d._status === 409 && d.code === 'no_register') { this.registerGone(); return; }
        if (d.code === 'out_of_stock' || d.code === 'stock_changed') {
          this.closeModal(true);
          if (d.variant_id) this.reconcile([{ variant_id: d.variant_id, available: d.available || '0', message: d.error }]);
          else this.reloadCartFromServer();
          this.toast(d.code === 'out_of_stock' ? (d.error + (d.product ? ' (' + d.product + ')' : '')) : STOCK_CHANGED, 'warning');
          this.search(true);
          return;
        }
        this.toast(d.error || 'Something went wrong. Please try again.', 'error');
      },
      reconcile: function (problems) {
        var self = this;
        problems.forEach(function (p) {
          var it = self.cart.find(function (i) { return i.variant_id === p.variant_id; });
          if (!it) return;
          var avail = self.num(p.available);
          if (avail <= 0) { self.cart = self.cart.filter(function (i) { return i !== it; }); self.notices.push(it.name + ' is out of stock and was removed.'); }
          else { it.quantity = avail; it.stock = avail; self.notices.push(it.name + ': only ' + self.qty(avail) + ' left. Quantity was reduced.'); }
        });
      },
      sendFeedback: function () {
        var self = this, f = this.fb;
        if (!f.rating) { f.error = 'Please tap a star first.'; return; }
        if (f.busy || f.sent || !this.lastSale || !this.lastSale.sale_id) return;
        f.busy = true; f.error = '';
        json(URLS.feedback, { sale_id: this.lastSale.sale_id, rating: f.rating, comment: f.comment }).then(function (d) {
          if (d.ok) { f.sent = true; } else { f.error = d.error || 'Could not save the feedback.'; }
        }).catch(function () { f.error = 'Network error. Please try again.'; }).then(function () { f.busy = false; });
      },
      newSale: function () { this.modal = null; this.focusSearch(); },
      printLast: function () {
        if (!this.lastSale) return;
        window.open(this.lastSale.print_url, '_blank', 'width=900,height=800');
      },

      // ------------------------------------------------------------------ returns
      // The invoice-number window: find the sale, then show it (and its return form) in the sale window - the POS page never changes.
      openReturn: function () {
        var self = this, ref = this.returnQuery.trim();
        if (!ref) return;
        json(URLS.sales.replace(/\/$/, '') + '/pos/find/?ref=' + encodeURIComponent(ref)).then(function (d) {
          if (d.ok) self.openSale(d.sale_id, self.returnMode === 'exchange' ? 'exchange' : 'return');
          else self.toast(d.error || 'Sale not found.', 'error');
        }).catch(function () { self.toast('Network error. Please try again.', 'error'); });
      },

      // ------------------------------------------------------------------ orders window (list -> one sale -> return / exchange)
      salesBase: function () { return URLS.sales.replace(/\/$/, '') + '/pos/'; },
      openOrders: function () { this.modal = 'orders'; this.ord.q = ''; this.ord.status = ''; this.loadOrders(true); },
      onOrdSearch: function () { var self = this; clearTimeout(this.ordTimer); this.ordTimer = setTimeout(function () { self.loadOrders(true); }, 250); },
      loadOrders: function (reset) {
        var self = this, o = this.ord;
        if (reset) { o.page = 1; o.list = []; }
        o.loading = true; o.error = '';
        return json(this.salesBase() + 'orders/?page=' + o.page + '&q=' + encodeURIComponent(o.q.trim()) + '&status=' + encodeURIComponent(o.status)).then(function (d) {
          if (!d.ok) { o.error = d.error || 'Could not load the orders.'; return; }
          o.list = o.list.concat(d.sales || []); o.more = !!d.has_more;
        }).catch(function () { o.error = 'Network error. Please try again.'; }).then(function () { o.loading = false; });
      },
      moreOrders: function () { this.ord.page += 1; this.loadOrders(false); },
      openSale: function (id, mode) {
        var self = this, o = this.ord;
        this.modal = 'sale'; o.sale = null; o.saleError = ''; o.saleLoading = true; o.mode = 'view'; o.formError = '';
        return json(this.salesBase() + 'sale/' + id + '/').then(function (d) {
          if (!d.ok) { o.saleError = d.error || 'Could not load this sale.'; return; }
          o.sale = d.sale; o.method = d.sale.default_method; o.restock = true; o.reason = ''; o.qty = {};
          if (mode && mode !== 'view') {
            if (d.sale.can_return) self.startReturn(mode); else o.saleError = d.sale.reference + ' is ' + d.sale.status_label.toLowerCase() + ' and cannot be returned.';
          }
        }).catch(function () { o.saleError = 'Network error. Please try again.'; }).then(function () { o.saleLoading = false; });
      },
      startReturn: function (mode) {
        var o = this.ord; o.mode = mode; o.formError = ''; o.qty = {}; o.reason = mode === 'exchange' ? 'Exchange' : '';
        this.returnMode = mode;
      },
      backToOrders: function () { this.modal = 'orders'; if (!this.ord.list.length) this.loadOrders(true); },
      retQty: function (l) { var n = this.num(this.ord.qty[l.id]); return n > 0 ? n : 0; },
      setRetQty: function (l, v) {
        var max = this.num(l.left), n = Math.max(0, Math.min(max, this.num(v)));
        this.ord.qty[l.id] = n > 0 ? String(n) : '';
      },
      stepRetQty: function (l, d) { this.setRetQty(l, this.retQty(l) + d); },
      retAll: function () { var self = this; (this.ord.sale ? this.ord.sale.lines : []).forEach(function (l) { self.setRetQty(l, self.num(l.left)); }); },
      retTotal: function () {
        var self = this, t = 0;
        (this.ord.sale ? this.ord.sale.lines : []).forEach(function (l) { t += self.retQty(l) * self.num(l.unit_refund); });
        return Math.round(t * 100) / 100;
      },
      retCount: function () { var self = this; return (this.ord.sale ? this.ord.sale.lines : []).reduce(function (a, l) { return a + (self.retQty(l) > 0 ? 1 : 0); }, 0); },
      submitReturn: function () {
        var self = this, o = this.ord;
        if (o.busy || !o.sale) return;
        if (!this.retCount()) { o.formError = 'Choose at least one item to take back.'; return; }
        if (!o.reason.trim()) { o.formError = 'Please give a reason.'; return; }
        var qty = {}; o.sale.lines.forEach(function (l) { if (self.retQty(l) > 0) qty[l.id] = String(self.retQty(l)); });
        var exchange = o.mode === 'exchange';
        o.busy = true; o.formError = '';
        json(this.salesBase() + 'return/', { sale_id: o.sale.id, qty: qty, reason: o.reason.trim(), method: o.method, restock: !!o.restock, exchange: exchange }).then(function (d) {
          if (!d.ok) { o.formError = d.error || 'The return could not be saved.'; self.beep(false); return; }
          self.beep(true);
          if (exchange) {
            self.exchangeInfo = { sale: d.sale, ret: d['return'], refund: d.refund };
            if (!self.cart.length && !self.saleNote) self.saleNote = 'Exchange for ' + d.sale + ' (return ' + d['return'] + ')';
            self.toast('Return ' + d['return'] + ' saved. Now add the new items for the exchange.', 'success');
            self.closeModal();
          } else {
            self.toast('Return ' + d['return'] + ' saved. Refund ' + self.fmt(d.refund) + '.', 'success');
            self.openSale(d.sale_id || o.sale.id, 'view');
          }
        }).catch(function () { o.formError = 'Network error. Please try again.'; }).then(function () { o.busy = false; });
      },
      printOrder: function (s) { window.open(s.print_url, '_blank', 'width=900,height=800'); },

      // ------------------------------------------------------------------ close register
      // ------------------------------------------------------------------ camera barcode scanning (phones / tablets / webcams)
      openCamera: function () {
        var self = this;
        if (!('BarcodeDetector' in window) || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          this.toast('This browser cannot scan with the camera. Use a barcode scanner, or type the code.', 'warning'); return;
        }
        this.cam.error = ''; this.cam.last = ''; this.cam.count = 0;
        this.modal = 'camera';
        try { this.cam.detector = new BarcodeDetector(); } catch (e) { this.cam.detector = null; this.cam.error = 'This device cannot read barcodes from the camera.'; return; }
        navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false }).then(function (stream) {
          if (self.modal !== 'camera') { stream.getTracks().forEach(function (t) { t.stop(); }); return; }
          self.cam.stream = stream;
          var v = self.$refs.camVideo; v.srcObject = stream; v.play().catch(function () { /* autoplay is allowed after the tap */ });
          self.camTick();
        }).catch(function (err) {
          self.cam.error = err && err.name === 'NotAllowedError' ? 'Camera permission was denied. Allow camera access for this site, then try again.'
            : 'Could not start the camera. Is another app using it?';
        });
      },
      camTick: function () {
        var self = this;
        clearTimeout(this.cam.timer);
        if (this.modal !== 'camera' || !this.cam.stream || !this.cam.detector) return;
        var v = this.$refs.camVideo;
        var next = function () { self.cam.timer = setTimeout(function () { self.camTick(); }, 250); };
        if (!v || v.readyState < 2) { next(); return; }
        this.cam.detector.detect(v).then(function (codes) {
          var code = codes && codes[0] && String(codes[0].rawValue || '').trim();
          var now = Date.now();
          if (code && (code !== self.cam.last || now - self.cam.lastAt > 2500)) {      // the same code held in front of the lens is added once
            self.cam.last = code; self.cam.lastAt = now;
            return self.submitQuery(code, false).then(function () { self.cam.count++; });
          }
        }).catch(function () { /* a bad frame: try the next one */ }).then(next);
      },
      stopCamera: function () {
        clearTimeout(this.cam.timer);
        if (this.cam.stream) { this.cam.stream.getTracks().forEach(function (t) { t.stop(); }); }
        this.cam.stream = null;
        var v = this.$refs.camVideo; if (v) v.srcObject = null;
      },
      openExpense: function () {
        this.exp = { category_id: '', amount: '', paid_to: '', note: '', busy: false, error: '' };
        this.modal = 'expense';
        var self = this;
        this.$nextTick(function () { var el = document.getElementById('exp-cat'); if (el) el.focus(); });
      },
      submitExpense: function () {
        var self = this, e = this.exp;
        if (e.busy) return;
        if (!e.category_id) { e.error = 'Choose an expense category.'; return; }
        if (!(self.num(e.amount) > 0)) { e.error = 'Enter the amount.'; return; }
        e.busy = true; e.error = '';
        json(URLS.expense, { category_id: e.category_id, amount: String(e.amount), paid_to: e.paid_to, note: e.note }).then(function (d) {
          if (d.ok) { self.closeModal(); self.toast('Expense ' + d.reference + ' saved (' + self.fmt(d.amount) + ' paid from the till).', 'success'); }
          else if (d._status === 409) { self.registerGone(); }
          else { e.error = d.error || 'Could not save the expense.'; }
        }).catch(function () { e.error = 'Network error. The expense was not saved.'; }).then(function () { e.busy = false; });
      },
      openClose: function () {
        var self = this;
        this.modal = 'close';
        this.closing = { loading: true, summary: null, error: '', counted: '', note: '', confirm: false };
        (this.dirtyUnsaved ? this.saveNow() : Promise.resolve()).then(function () { return json(URLS.summary); }).then(function (d) {
          if (d.ok) { self.closing.summary = d.summary; self.$nextTick(function () { if (self.$refs.countedInput) self.$refs.countedInput.focus(); }); }
          else if (d._status === 409) self.registerGone();
          else self.closing.error = d.error || 'Could not load the register summary.';
        }).catch(function () { self.closing.error = 'Could not load the register summary. Check your connection.'; })
          .then(function () { self.closing.loading = false; });
      },
      reviewClose: function () {
        var c = this.closing;
        if (!c.summary) return;
        var v = parseFloat(c.counted);
        if (c.counted === '' || !isFinite(v) || v < 0) { c.error = 'Enter the cash you counted (0 or more).'; return; }
        c.error = ''; c.confirm = true;
      },
      confirmClose: function () {
        var self = this, c = this.closing, f = new FormData();
        f.append('closing_cash_counted', c.counted); f.append('note', c.note || '');
        this.busy = true;
        json(URLS.close, f).then(function (d) {
          if (d.ok) {
            lsDel(BACKUP_KEY); self.dirtyUnsaved = false;
            self.toast(d.message || 'Register closed.', 'success');
            setTimeout(function () { window.location.href = d.redirect || URLS.counters; }, 700);
          } else {
            c.confirm = false; c.error = d.error || 'Could not close the register.';
            if (d.redirect) setTimeout(function () { window.location.href = d.redirect; }, 1200);
          }
        }).catch(function () { c.confirm = false; c.error = 'Network error. The register was not closed.'; }).then(function () { self.busy = false; });
      },

      // ------------------------------------------------------------------ modals + keyboard
      closeModal: function (keepFocus) {
        if (this.busy && this.modal === 'payment') return;
        if (this.modal === 'camera') this.stopCamera();
        this.modal = null;
        if (!keepFocus) this.focusSearch();
      },
      onKeydown: function (e) {
        var key = e.key, mod = e.ctrlKey || e.metaKey, typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || ''));
        if (this.confirmBox.open) return;
        if (key === 'Enter' && mod) {
          e.preventDefault();
          if (this.modal === 'payment') this.completeSale();
          else if (this.modal === 'success') this.newSale();
          else if (!this.modal) this.openPayment();
          return;
        }
        if (key === 'F2' || (mod && key.toLowerCase() === 'k')) {
          e.preventDefault();
          if (!this.modal || this.modal === 'help') { this.modal = null; var s = this.$refs.searchInput; if (s) { s.focus(); s.select(); } }
          return;
        }
        if (key === 'Escape' && !this.modal) {
          if (this.showCustomers) { this.showCustomers = false; return; }
          if (this.query) { this.query = ''; this.search(); }
          return;
        }
        if (this.modal && this.modal !== 'help') return;                    // the open dialog owns the keyboard
        // a scanner types into whatever has focus: if that is not a text box, send the characters to the search box
        if (!typing && !mod && !e.altKey && key.length === 1 && key !== '?' && key !== ' ' && this.$refs.searchInput) this.$refs.searchInput.focus();
        if (key === 'F4') { e.preventDefault(); this.focusCustomer(); }
        else if (key === 'F6') { e.preventDefault(); if (this.cart.length) { this.discountDraft = Object.assign({}, this.orderDiscount); this.modal = 'discount'; } }
        else if (key === 'F7') { e.preventDefault(); this.holdSale(); }
        else if (key === 'F8') { e.preventDefault(); this.openPayment(); }
        else if (key === 'F9') { e.preventDefault(); this.openHeld(); }
        else if (mod && e.shiftKey && key.toLowerCase() === 'x') { e.preventDefault(); this.clearCart(); }
        else if (key === '?' && !typing) { e.preventDefault(); this.modal = 'help'; }
      },
    };
  };
})();
