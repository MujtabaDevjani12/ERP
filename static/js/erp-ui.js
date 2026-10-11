/* ==========================================================================
   ERP UI behaviours — shared by the ERP shell and the POS screen.
   Registers Alpine stores/components (theme, sidebar, toasts, confirm dialog, drawer, command palette)
   and a few small vanilla helpers (button loading state, tooltips, shortcuts, lazy DataTables).
   Must be loaded BEFORE Alpine (both are `defer`, so keep this tag first).
   ========================================================================== */
(function () {
  'use strict';

  var root = document.documentElement;
  var LS = {
    get: function (k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  };
  var ERP = window.ERP = window.ERP || {};

  // Safety net for browsers without `overflow: clip`: if anything (focus, scrollIntoView) scrolls the fixed app shell,
  // put it straight back so the sidebar/top bar never slide out of view and leave the page "cut off".
  document.addEventListener('scroll', function (e) {
    var t = e.target;
    if (t && t.classList && t.classList.contains('app-shell')) { t.scrollTop = 0; t.scrollLeft = 0; }
  }, true);
  ERP.storage = LS;

  function isTyping(el) {
    if (!el) return false;
    var t = (el.tagName || '').toLowerCase();
    return t === 'input' || t === 'textarea' || t === 'select' || el.isContentEditable;
  }

  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    if (m) return m.getAttribute('content');
    var i = document.querySelector('input[name=csrfmiddlewaretoken]');
    if (i) return i.value;
    var c = document.cookie.match(/csrftoken=([^;]+)/);
    return c ? decodeURIComponent(c[1]) : '';
  }
  ERP.csrf = csrfToken;

  /* ---------- Alpine: stores & components ---------- */
  document.addEventListener('alpine:init', function () {
    var Alpine = window.Alpine;

    Alpine.store('ui', {
      theme: root.getAttribute('data-theme') || 'light',
      collapsed: root.classList.contains('sidebar-collapsed'),
      mobileOpen: false,
      density: root.getAttribute('data-density') || 'comfortable',
      toggleTheme: function () { this.setTheme(this.theme === 'dark' ? 'light' : 'dark'); },
      setTheme: function (t) {
        this.theme = t; root.setAttribute('data-theme', t); LS.set('ui.theme', t);
        window.dispatchEvent(new CustomEvent('ui-theme', { detail: t }));
      },
      toggleSidebar: function () {
        if (window.innerWidth < 1024) { this.mobileOpen = !this.mobileOpen; return; }
        this.collapsed = !this.collapsed;
        root.classList.toggle('sidebar-collapsed', this.collapsed);
        LS.set('ui.sidebar', this.collapsed ? 'collapsed' : 'expanded');
      },
      setDensity: function (d) { this.density = d; root.setAttribute('data-density', d); LS.set('ui.density', d); },
      groupOpen: function (key, dflt) { var v = LS.get('ui.nav.' + key, null); return v === null ? dflt : v === '1'; },
      setGroup: function (key, open) { LS.set('ui.nav.' + key, open ? '1' : '0'); }
    });

    /* Toasts */
    Alpine.store('toasts', {
      items: [], seq: 0,
      push: function (type, message, opts) {
        var self = this, id = ++this.seq;
        this.items.push({ id: id, type: type, message: message });
        var ms = (opts && opts.duration) || (type === 'error' ? 7000 : 4000);
        setTimeout(function () { self.remove(id); }, ms);
        return id;
      },
      remove: function (id) { this.items = this.items.filter(function (t) { return t.id !== id; }); }
    });
    window.toast = {
      success: function (m, o) { return Alpine.store('toasts').push('success', m, o); },
      error: function (m, o) { return Alpine.store('toasts').push('error', m, o); },
      warning: function (m, o) { return Alpine.store('toasts').push('warning', m, o); },
      info: function (m, o) { return Alpine.store('toasts').push('info', m, o); }
    };

    /* Confirm dialog (replaces window.confirm) */
    Alpine.store('confirm', {
      open: false, title: '', message: '', confirmLabel: 'Confirm', cancelLabel: 'Cancel', tone: 'danger', _resolve: null,
      ask: function (o) {
        var self = this;
        this.title = o.title || 'Are you sure?'; this.message = o.message || '';
        this.confirmLabel = o.confirmLabel || 'Confirm'; this.cancelLabel = o.cancelLabel || 'Cancel';
        this.tone = o.tone || 'danger'; this.open = true;
        return new Promise(function (resolve) { self._resolve = resolve; });
      },
      answer: function (v) { this.open = false; var r = this._resolve; this._resolve = null; if (r) r(v); }
    });
    window.confirmDialog = function (o) { return Alpine.store('confirm').ask(o); };

    /* Right-hand drawer: loads an HTML fragment, submits its form with fetch */
    Alpine.store('drawer', {
      open: false, title: '', html: '', loading: false, error: '', url: '', saving: false, onDone: null,
      load: function (url, opts) {
        var self = this;
        this.open = true; this.loading = true; this.html = ''; this.error = ''; this.url = url;
        this.title = (opts && opts.title) || ''; this.onDone = (opts && opts.onDone) || null;
        return fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
          .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
          .then(function (t) { self.html = t; })
          .catch(function () { self.error = 'This panel could not be loaded. Please try again.'; })
          .then(function () { self.loading = false; });
      },
      close: function () { this.open = false; this.html = ''; this.onDone = null; },
      submit: function (form) {
        var self = this;
        if (this.saving) return;
        this.saving = true;
        fetch(form.getAttribute('action') || this.url, {
          method: 'POST', body: new FormData(form), credentials: 'same-origin',
          headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': csrfToken() }
        }).then(function (r) {
          var ct = r.headers.get('content-type') || '';
          if (ct.indexOf('application/json') !== -1) {
            return r.json().then(function (data) {
              if (data.ok) {
                if (data.message) window.toast.success(data.message);
                var cb = self.onDone; self.close();
                if (cb) cb(data); else if (data.reload !== false) window.location.reload();
              } else { self.error = data.error || 'Could not save.'; }
            });
          }
          return r.text().then(function (t) { self.html = t; });
        }).catch(function () { self.error = 'Could not save. Check your connection and try again.'; })
          .then(function () { self.saving = false; });
      }
    });

    /* Notification bell: mark one / all read without leaving the page */
    /* Row "more actions" menu: the panel is teleported to <body> and positioned with fixed coordinates, so it is never
       clipped by a scrolling table wrapper and flips upward near the bottom of the window. */
    Alpine.data('rowMenu', function () {
      return {
        open: false, pos: '', btn: null,
        toggle: function (el) {
          if (this.open) { this.open = false; return; }
          this.btn = el;
          var r = el.getBoundingClientRect(), w = 236, vh = window.innerHeight;
          var left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w));
          var flip = (vh - r.bottom) < 300 && r.top > (vh - r.bottom);
          this.pos = 'left:' + left + 'px;' + (flip ? 'bottom:' + (vh - r.top + 6) + 'px' : 'top:' + (r.bottom + 6) + 'px');
          this.open = true;
        },
        away: function (e) { if (this.btn && this.btn.contains(e.target)) return; this.open = false; }
      };
    });

    Alpine.data('bell', function (cfg) {
      function post(url) {
        return fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': csrfToken() } })
          .then(function (r) { return r.json(); });
      }
      return {
        open: false, unread: cfg.unread || 0,
        markAll: function () {
          var self = this;
          post(cfg.readAllUrl).then(function (d) {
            self.unread = d.unread || 0;
            self.$root.querySelectorAll('.notif-item.is-unread').forEach(function (e) { e.classList.remove('is-unread'); });
          }).catch(function () { window.toast.error('Could not update notifications. Please try again.'); });
        },
        openItem: function (ev) {
          var a = ev.currentTarget, url = a.getAttribute('data-read-url'), href = a.getAttribute('href');
          if (!url) return;                                   // already read: follow the link normally
          ev.preventDefault();
          post(url).catch(function () { /* still navigate */ }).then(function () { window.location.href = href; });
        }
      };
    });

    /* ERP list pages: column visibility + saved filter views (both remembered in localStorage, per register) */
    Alpine.data('listTools', function () {
      function load(k, d) { try { var v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } }
      function save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode: not remembered */ } }
      return {
        key: '', query: '', listUrl: '', hidden: [], saved: [], newName: '', colMenu: false, filtMenu: false,
        init: function () {
          var d = this.$el.dataset;
          this.key = d.key || ''; this.query = d.query || ''; this.listUrl = d.listUrl || '';
          this.hidden = load('ui.cols.' + this.key, []);
          this.saved = load('ui.filters.' + this.key, []);
          this.apply();
        },
        isHidden: function (i) { return this.hidden.indexOf(i) !== -1; },
        toggleCol: function (i) {
          var at = this.hidden.indexOf(i);
          if (at === -1) this.hidden.push(i); else this.hidden.splice(at, 1);
          save('ui.cols.' + this.key, this.hidden); this.apply();
        },
        resetCols: function () { this.hidden = []; save('ui.cols.' + this.key, this.hidden); this.apply(); },
        apply: function () {
          var self = this;
          this.$el.querySelectorAll('[data-col]').forEach(function (c) {
            c.style.display = self.isHidden(parseInt(c.getAttribute('data-col'), 10)) ? 'none' : '';
          });
        },
        saveCurrent: function () {
          var name = (this.newName || '').trim();
          if (!name) { window.toast.warning('Give the view a name first.'); return; }
          if (!this.query) { window.toast.warning('Apply a search or filter first, then save it.'); return; }
          this.saved = this.saved.filter(function (f) { return f.name !== name; }).concat([{ name: name, query: this.query }]);
          save('ui.filters.' + this.key, this.saved); this.newName = '';
          window.toast.success('View saved on this device.');
        },
        removeSaved: function (i) { this.saved.splice(i, 1); save('ui.filters.' + this.key, this.saved); },
        go: function (f) { window.location.href = this.listUrl + '?' + f.query; }
      };
    });

    /* Command palette (Ctrl/Cmd + K) */
    Alpine.data('palette', function () {
      return {
        open: false, q: '', index: 0, remote: [], loading: false, timer: null, ctrl: null,
        commands: [], recents: [], searchUrl: '',
        init: function () {
          var self = this, el = document.getElementById('ui-commands');
          try { this.commands = el ? JSON.parse(el.textContent) || [] : []; } catch (e) { this.commands = []; }
          try { this.recents = JSON.parse(LS.get('ui.recent', '[]')) || []; } catch (e) { this.recents = []; }
          this.searchUrl = document.body.getAttribute('data-search-url') || '';
          window.addEventListener('keydown', function (e) {
            if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'k') { e.preventDefault(); self.toggle(); }
          });
          window.addEventListener('ui-palette', function () { self.show(); });
        },
        show: function () {
          var self = this;
          this.open = true; this.q = ''; this.index = 0; this.remote = [];
          this.$nextTick(function () { if (self.$refs.input) self.$refs.input.focus(); });
        },
        close: function () { this.open = false; },
        toggle: function () { if (this.open) this.close(); else this.show(); },
        onInput: function () {
          var self = this;
          this.index = 0;
          clearTimeout(this.timer);
          if (this.ctrl) { this.ctrl.abort(); this.ctrl = null; }
          if (!this.searchUrl || this.q.trim().length < 2) { this.remote = []; this.loading = false; return; }
          this.loading = true;
          this.timer = setTimeout(function () {
            self.ctrl = window.AbortController ? new AbortController() : null;
            fetch(self.searchUrl + '?q=' + encodeURIComponent(self.q.trim()), {
              credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' }, signal: self.ctrl ? self.ctrl.signal : undefined
            }).then(function (r) { return r.json(); })
              .then(function (d) { self.remote = (d.results || []).map(function (x, i) { return { key: 'r' + i, section: x.group, title: x.title, hint: x.subtitle || '', url: x.url }; }); self.loading = false; })
              .catch(function (e) { if (e && e.name === 'AbortError') return; self.remote = []; self.loading = false; });
          }, 200);
        },
        get items() {
          var q = this.q.trim().toLowerCase(), out = [], i = 0;
          function match(c) { return (c.title + ' ' + (c.keywords || '') + ' ' + (c.section || '')).toLowerCase().indexOf(q) !== -1; }
          if (!q) {
            this.recents.slice(0, 5).forEach(function (r) { out.push({ key: 'rec' + (i++), section: 'Recent', title: r.t, hint: r.s || '', url: r.u }); });
            this.commands.filter(function (c) { return c.kind === 'action'; }).slice(0, 8).forEach(function (c) { out.push({ key: 'a' + (i++), section: 'Quick actions', title: c.title, hint: c.hint || '', url: c.url, action: c.action }); });
            this.commands.filter(function (c) { return c.kind === 'nav'; }).slice(0, 10).forEach(function (c) { out.push({ key: 'n' + (i++), section: 'Navigate', title: c.title, hint: c.hint || '', url: c.url, action: c.action }); });
            return out;
          }
          out = out.concat(this.remote);
          this.commands.filter(function (c) { return c.kind === 'action' && match(c); }).slice(0, 6).forEach(function (c) { out.push({ key: 'a' + (i++), section: 'Quick actions', title: c.title, hint: c.hint || '', url: c.url, action: c.action }); });
          this.commands.filter(function (c) { return c.kind === 'nav' && match(c); }).slice(0, 8).forEach(function (c) { out.push({ key: 'n' + (i++), section: 'Navigate', title: c.title, hint: c.hint || '', url: c.url, action: c.action }); });
          return out;
        },
        move: function (d) {
          var n = this.items.length; if (!n) return;
          this.index = (this.index + d + n) % n;
          var self = this;
          this.$nextTick(function () { var el = self.$root.querySelector('[data-idx="' + self.index + '"]'); if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' }); });
        },
        choose: function (it) {
          if (!it) it = this.items[this.index];
          if (!it) return;
          if (it.action === 'theme') { this.close(); Alpine.store('ui').toggleTheme(); return; }
          if (it.action === 'shortcuts') { this.close(); window.dispatchEvent(new CustomEvent('ui-shortcuts')); return; }
          if (!it.url) return;
          if (it.section !== 'Recent' && it.section !== 'Navigate' && it.section !== 'Quick actions') {
            var rec = this.recents.filter(function (r) { return r.u !== it.url; });
            rec.unshift({ t: it.title, u: it.url, s: it.section });
            LS.set('ui.recent', JSON.stringify(rec.slice(0, 8)));
          }
          this.close();
          window.location.href = it.url;
        }
      };
    });
  });

  /* Server-side Django messages -> toasts */
  document.addEventListener('alpine:initialized', function () {
    var el = document.getElementById('ui-messages');
    if (!el || !window.toast) return;
    try {
      (JSON.parse(el.textContent) || []).forEach(function (m) {
        var tags = (m.tags || '').split(' ');
        var type = tags.indexOf('error') !== -1 ? 'error' : tags.indexOf('success') !== -1 ? 'success' : tags.indexOf('warning') !== -1 ? 'warning' : 'info';
        window.toast[type](m.message);
      });
    } catch (e) { /* ignore */ }
  });

  /* ---------- data-confirm: nice dialog instead of window.confirm ---------- */
  function confirmOptions(el) {
    var parts = (el.getAttribute('data-confirm') || '').split('|');
    var title = parts.length > 1 ? parts[0] : 'Are you sure?';
    var message = parts.length > 1 ? parts[1] : parts[0];
    return { title: title, message: message, confirmLabel: parts[2] || el.getAttribute('data-confirm-label') || 'Confirm', tone: el.getAttribute('data-confirm-tone') || 'danger' };
  }
  document.addEventListener('click', function (e) {
    var el = e.target.closest ? e.target.closest('[data-confirm]') : null;
    if (!el || el.tagName === 'FORM' || el.dataset.confirmed === '1') return;
    e.preventDefault(); e.stopPropagation();
    window.confirmDialog(confirmOptions(el)).then(function (ok) {
      if (!ok) return;
      el.dataset.confirmed = '1';
      if (el.tagName === 'A' && el.href) { window.location.href = el.href; } else { el.click(); }
      setTimeout(function () { delete el.dataset.confirmed; }, 50);
    });
  }, true);
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (!f.getAttribute || !f.hasAttribute('data-confirm') || f.dataset.confirmed === '1') return;
    e.preventDefault(); e.stopImmediatePropagation();
    var submitter = e.submitter;
    window.confirmDialog(confirmOptions(f)).then(function (ok) {
      if (!ok) return;
      f.dataset.confirmed = '1';
      if (f.requestSubmit) { submitter ? f.requestSubmit(submitter) : f.requestSubmit(); } else { f.submit(); }
      setTimeout(function () { delete f.dataset.confirmed; }, 50);
    });
  }, true);

  /* ---------- Button loading state + duplicate-submit guard ---------- */
  function resetSubmitting(scope) {
    (scope || document).querySelectorAll('form[data-submitting]').forEach(function (f) { delete f.dataset.submitting; });
    (scope || document).querySelectorAll('.is-loading[data-was-submit]').forEach(function (b) { b.classList.remove('is-loading'); b.removeAttribute('aria-busy'); });
  }
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (e.defaultPrevented || !f.getAttribute || f.hasAttribute('data-no-loading') || (f.getAttribute('method') || 'get').toLowerCase() === 'get') return;
    if (f.dataset.submitting === '1') { e.preventDefault(); return; }
    f.dataset.submitting = '1';
    var btn = e.submitter || f.querySelector('button[type=submit], button:not([type]), input[type=submit]');
    if (btn && btn.tagName === 'BUTTON') { btn.classList.add('is-loading'); btn.setAttribute('aria-busy', 'true'); btn.setAttribute('data-was-submit', '1'); }
    setTimeout(function () { resetSubmitting(f); }, 12000);   // downloads never navigate away
  });
  window.addEventListener('pageshow', function () { resetSubmitting(); });

  /* ---------- drawer helpers: [data-drawer-url] opens a drawer; forms inside submit by fetch ---------- */
  document.addEventListener('click', function (e) {
    var el = e.target.closest ? e.target.closest('[data-drawer-url]') : null;
    if (!el || !window.Alpine) return;
    e.preventDefault();
    window.Alpine.store('drawer').load(el.getAttribute('data-drawer-url'), { title: el.getAttribute('data-drawer-title') || '' });
  });
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (!f.closest || !f.closest('[data-drawer-body]') || !window.Alpine) return;
    e.preventDefault(); e.stopImmediatePropagation();
    window.Alpine.store('drawer').submit(f);
  }, true);

  /* ---------- Tooltips ([data-tip]; nav items only when the sidebar is collapsed) ---------- */
  var tip;
  function tipEl() { if (!tip) { tip = document.createElement('div'); tip.id = 'ui-tip'; tip.setAttribute('role', 'tooltip'); document.body.appendChild(tip); } return tip; }
  document.addEventListener('mouseover', function (e) {
    var el = e.target.closest ? e.target.closest('[data-tip]') : null;
    if (!el) return;
    if (el.hasAttribute('data-tip-collapsed') && !root.classList.contains('sidebar-collapsed')) return;
    if (window.innerWidth < 1024 && el.hasAttribute('data-tip-collapsed')) return;
    var t = tipEl(), r = el.getBoundingClientRect();
    t.textContent = el.getAttribute('data-tip');
    t.style.left = (r.right + 10) + 'px'; t.style.top = (r.top + r.height / 2 - 14) + 'px';
    if (el.getAttribute('data-tip-pos') === 'bottom') { t.style.left = (r.left + r.width / 2 - t.offsetWidth / 2) + 'px'; t.style.top = (r.bottom + 8) + 'px'; }
    t.classList.add('show');
  });
  document.addEventListener('mouseout', function (e) {
    if (tip && e.target.closest && e.target.closest('[data-tip]')) tip.classList.remove('show');
  });

  /* ---------- Collapsed sidebar: group buttons open a fly-out menu next to the icon rail ---------- */
  (function () {
    var side = document.getElementById('app-sidebar');
    if (!side) return;
    var pop = null, timer = null, current = null;
    function isRail() { return window.innerWidth >= 1024 && root.classList.contains('sidebar-collapsed'); }
    function ensure() {
      if (pop) return pop;
      pop = document.createElement('div'); pop.className = 'rail-pop'; pop.setAttribute('role', 'menu');
      document.body.appendChild(pop);
      pop.addEventListener('mouseenter', function () { clearTimeout(timer); });
      pop.addEventListener('mouseleave', function () { schedule(); });
      return pop;
    }
    function hide(now) {
      clearTimeout(timer);
      var run = function () { if (pop) pop.classList.remove('show'); current = null; };
      if (now) run(); else timer = setTimeout(run, 160);
    }
    function schedule() { if (!(pop && pop.contains(document.activeElement))) hide(false); }
    function show(btn, focusFirst) {
      if (!isRail()) return;
      clearTimeout(timer);
      var group = btn.closest('.nav-group'); if (!group) return;
      var p = ensure(); current = btn;
      p.setAttribute('aria-label', group.getAttribute('data-group-label') || '');
      p.textContent = '';
      var t = document.createElement('div'); t.className = 'rp-title'; t.textContent = group.getAttribute('data-group-label') || ''; p.appendChild(t);
      Array.prototype.forEach.call(group.querySelectorAll('.nav-children > div > *'), function (n) {
        if (n.tagName === 'A') {
          var a = document.createElement('a'); a.href = n.getAttribute('href'); a.textContent = n.textContent.trim(); a.setAttribute('role', 'menuitem');
          if (n.classList.contains('is-active')) a.className = 'is-active';
          p.appendChild(a);
        } else {
          var l = document.createElement('div'); l.className = 'rp-sub'; l.textContent = n.textContent.trim(); p.appendChild(l);
        }
      });
      var r = btn.getBoundingClientRect(), sr = side.getBoundingClientRect();
      p.style.left = Math.round(sr.right + 8) + 'px'; p.style.top = Math.round(r.top) + 'px';
      p.classList.add('show');
      var h = p.offsetHeight;
      p.style.top = Math.round(Math.max(8, Math.min(r.top, window.innerHeight - h - 8))) + 'px';
      if (focusFirst) { var f = p.querySelector('a'); if (f) f.focus(); }
    }
    side.addEventListener('mouseover', function (e) {
      var b = e.target.closest && e.target.closest('.nav-group-btn');
      if (b && isRail()) show(b, false); else if (isRail() && pop && pop.classList.contains('show') && !(e.target.closest && e.target.closest('.nav-group'))) schedule();
    });
    side.addEventListener('mouseleave', function () { schedule(); });
    side.addEventListener('focusin', function (e) { var b = e.target.closest && e.target.closest('.nav-group-btn'); if (b && isRail()) show(b, false); });
    side.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('.nav-group-btn');
      if (b && isRail()) { show(b, e.detail === 0); }
    });
    document.addEventListener('click', function (e) { if (pop && pop.classList.contains('show') && !pop.contains(e.target) && !side.contains(e.target)) hide(true); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && pop && pop.classList.contains('show')) { var c = current; hide(true); if (c) c.focus({ preventScroll: true }); } });
    window.addEventListener('resize', function () { hide(true); });
    var sc = side.querySelector('.app-sidebar-scroll'); if (sc) sc.addEventListener('scroll', function () { hide(true); }, { passive: true });
  })();

  /* ---------- Keyboard shortcuts ---------- */
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      var f = document.querySelector('form[data-ctrl-s]');
      if (f) { e.preventDefault(); if (f.requestSubmit) f.requestSubmit(); else f.submit(); }
      return;
    }
    if (e.key === '?' && !isTyping(document.activeElement) && !e.ctrlKey && !e.metaKey) {
      e.preventDefault(); window.dispatchEvent(new CustomEvent('ui-shortcuts'));
    }
  });

  /* ---------- Row click-through for lists: <tr data-href="..."> ---------- */
  document.addEventListener('click', function (e) {
    var tr = e.target.closest ? e.target.closest('tr[data-href]') : null;
    if (!tr || e.target.closest('a, button, input, select, label, textarea, [data-no-row]')) return;
    var sel = window.getSelection && window.getSelection().toString();
    if (sel) return;
    if (e.ctrlKey || e.metaKey) window.open(tr.getAttribute('data-href'), '_blank'); else window.location.href = tr.getAttribute('data-href');
  });

  /* ---------- Bulk selection on ui tables ---------- */
  document.addEventListener('change', function (e) {
    var t = e.target;
    if (!t.matches) return;
    var table = t.closest('table');
    if (t.matches('[data-select-all]') && table) {
      table.querySelectorAll('input[data-select-row]').forEach(function (c) { c.checked = t.checked; c.closest('tr').classList.toggle('is-selected', t.checked); });
    } else if (t.matches('[data-select-row]')) {
      t.closest('tr').classList.toggle('is-selected', t.checked);
    } else { return; }
    var form = t.closest('form'); if (!form) return;
    var n = form.querySelectorAll('input[data-select-row]:checked').length;
    var bar = form.querySelector('[data-bulkbar]');
    if (bar) { bar.style.display = n ? 'flex' : 'none'; var c = bar.querySelector('[data-bulk-count]'); if (c) c.textContent = n; }
  });

  /* ---------- Lazy-load DataTables only on pages that use <table class="datatable"> ---------- */
  var CDN = 'https://cdnjs.cloudflare.com/ajax/libs/';
  var DT_CSS = [CDN + 'datatables/1.13.11/css/jquery.dataTables.min.css', CDN + 'datatables-buttons/2.4.2/css/buttons.dataTables.min.css'];
  var DT_JS = [CDN + 'jquery/3.7.1/jquery.min.js', CDN + 'datatables/1.13.11/js/jquery.dataTables.min.js', CDN + 'datatables-buttons/2.4.2/js/dataTables.buttons.min.js',
    CDN + 'jszip/3.10.1/jszip.min.js', CDN + 'pdfmake/0.2.7/pdfmake.min.js', CDN + 'pdfmake/0.2.7/vfs_fonts.js',
    CDN + 'datatables-buttons/2.4.2/js/buttons.html5.min.js', CDN + 'datatables-buttons/2.4.2/js/buttons.print.min.js'];
  function loadScript(src) {
    return new Promise(function (res, rej) { var s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = rej; document.head.appendChild(s); });
  }
  function initDataTables() {
    var tables = document.querySelectorAll('table.datatable');
    if (!tables.length) return;
    DT_CSS.forEach(function (h) { var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = h; document.head.appendChild(l); });
    tables.forEach(function (t) { if (t.parentElement && !t.closest('.datatable-card')) { var w = document.createElement('div'); w.className = 'datatable-card'; t.parentNode.insertBefore(w, t); w.appendChild(t); } });
    DT_JS.reduce(function (p, src) { return p.then(function () { return loadScript(src); }); }, Promise.resolve()).then(function () {
      var $ = window.jQuery; if (!$) return;
      $('table.datatable').each(function () {
        var $t = $(this); $t.addClass('stripe hover');
        $t.DataTable({
          pageLength: 10, lengthMenu: [[10, 25, 50, 100, -1], [10, 25, 50, 100, 'All']], order: [],
          language: { search: '', searchPlaceholder: 'Search records…', lengthMenu: 'Show _MENU_', info: 'Showing _START_ to _END_ of _TOTAL_ entries', infoEmpty: 'No entries found', infoFiltered: '(filtered from _MAX_ total)', emptyTable: 'No records available in table', paginate: { previous: '‹ Prev', next: 'Next ›' } },
          dom: "<'flex flex-wrap items-center justify-between gap-3 mb-3'<'flex items-center gap-2.5'lB>f>t<'flex flex-wrap items-center justify-between gap-3 mt-4'ip>",
          buttons: [{ extend: 'copyHtml5', text: 'Copy' }, { extend: 'csvHtml5', text: 'CSV' }, { extend: 'excelHtml5', text: 'Excel' }, { extend: 'pdfHtml5', text: 'PDF', orientation: 'landscape' }, { extend: 'print', text: 'Print' }],
          columnDefs: [{ orderable: false, targets: 'no-sort' }]
        });
      });
    }).catch(function () { /* offline: the plain table is still usable */ });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initDataTables); else initDataTables();

  /* F2 / F5 = open POS from anywhere. F5 is only taken when nothing is being typed; Ctrl+R, Ctrl+F5 and Shift+F5 still reload the page. */
  document.addEventListener('keydown', function (e) {
    var f5 = e.key === 'F5' && !e.ctrlKey && !e.metaKey && !e.shiftKey && !isTyping(document.activeElement);
    if ((e.key === 'F2' || f5) && !document.body.hasAttribute('data-pos-screen')) {
      var url = document.body.getAttribute('data-pos-url');
      if (url) { e.preventDefault(); window.location.href = url; }
    }
  });
})();
