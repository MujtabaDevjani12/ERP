/* POS billing screen. Talks only to /api/v1/pos/* (session cookie + CSRF).
   Totals: instant local estimate, then replaced by the server's authoritative calculation (/pos/calculate/),
   which uses the same code as checkout - the receipt can never disagree with what was shown. */
(function () {
  "use strict";
  const App = window.App;
  const $ = (id) => document.getElementById(id);
  const API = "/api/v1/pos/";
  const ICON = { cash: "cash-coin", card: "credit-card-2-front", cheque: "journal-text", gift_card: "gift", deposit: "wallet2", bank: "bank", wallet: "phone", other: "three-dots", cc: "credit-card-2-front", ppp: "paypal", stripe: "credit-card", authorize: "credit-card" };

  const S = {
    cfg: null, categories: [], category: null, q: "", offset: 0, total: 0, loading: false, products: [], gridCtrl: null, sugCtrl: null, sugItems: [], sugIndex: -1,
    cart: [], customer: null, orderDiscount: "", orderTaxId: null, shipping: 0,
    srv: null, srvFresh: false, version: 0, calcErr: "", timer: null, idem: App.uuid(), register: null, heldCount: 0, warehouse: null, paying: false,
  };
  const r4 = (x) => App.round(x, 4);
  const num = (v) => { const n = parseFloat(v); return Number.isFinite(n) ? n : 0; };
  let uidSeq = 0;

  /* ---------------------------------------------------------------- local calculation (mirrors apps/sales/calculator.py) */
  function discountOf(base, text) {
    const t = String(text || "").trim(); if (!t) return 0;
    return t.includes("%") ? r4((base * num(t)) / 100) : r4(num(t));
  }
  function roundNumber(v, mode) {
    switch (mode) {
      case 1: return Math.round(v * 20) / 20;
      case 2: return Math.round(v * 2) / 2;
      case 3: return Math.round(v);
      case 4: return Math.ceil(v);
      default: return v;
    }
  }
  function calcLine(l) {
    const entered = l.custom_price != null ? num(l.custom_price) : num(l.price);
    const dpu = Math.min(discountOf(entered, l.discount), entered);
    const price = r4(entered - dpu);
    let taxPU = 0, net = price; const t = l.tax_rate;
    if (t && num(t.rate) !== 0) {
      const rate = num(t.rate);
      if (t.type === "fixed") taxPU = r4(rate);
      else if (l.tax_method === 1) taxPU = r4((price * rate) / 100);
      else { taxPU = r4((price * rate) / (100 + rate)); net = r4(price - taxPU); }
    }
    const netTotal = r4(net * l.qty), tax = r4(taxPU * l.qty);
    return { unit_price: entered, discount: r4(dpu * l.qty), net_unit_price: net, tax, subtotal: r4(netTotal + tax), net_total: netTotal };
  }
  function calcLocal() {
    const lines = S.cart.map(calcLine);
    const total = r4(lines.reduce((a, l) => a + l.net_total, 0)), ptax = r4(lines.reduce((a, l) => a + l.tax, 0));
    const pdisc = r4(lines.reduce((a, l) => a + l.discount, 0));
    const od = Math.min(discountOf(total + ptax, S.orderDiscount), total + ptax);
    const ot = S.cfg.order_tax_enabled && S.orderTaxId ? S.cfg.tax_rates.find((t) => t.id === S.orderTaxId) : null;
    let otax = 0; if (ot && num(ot.rate)) otax = ot.type === "fixed" ? r4(num(ot.rate)) : r4(((total + ptax - od) * num(ot.rate)) / 100);
    const ship = r4(num(S.shipping)), grand = r4(total + ptax + otax + ship - od);
    const rounding = S.cfg.rounding ? r4(roundNumber(grand, S.cfg.rounding) - grand) : 0;
    return { lines, total, product_discount: pdisc, product_tax: ptax, order_discount: od, order_tax: otax, total_tax: r4(ptax + otax), shipping: ship, grand_total: grand, rounding, payable: r4(grand + rounding), total_items: S.cart.reduce((a, l) => a + l.qty, 0) };
  }
  const T = () => (S.srvFresh && S.srv ? S.srv : calcLocal());

  /* ---------------------------------------------------------------- server totals */
  function payload(extra) {
    return Object.assign({
      customer_id: S.customer && S.customer.id, warehouse_id: S.warehouse, biller_id: S.cfg.default_biller, order_discount: S.orderDiscount || "",
      order_tax_rate_id: S.orderTaxId, shipping: String(num(S.shipping)),
      lines: S.cart.map((l) => { const o = { product_id: l.product_id, quantity: String(l.qty), discount: l.discount || "" }; if (l.variant_id) o.variant_id = l.variant_id; if (l.custom_price != null) o.unit_price = String(l.custom_price); return o; }),
    }, extra || {});
  }
  function changed() {
    S.version += 1; S.srvFresh = false; clearTimeout(S.timer); S.persist && S.persist();
    render();
    if (!S.cart.length || !S.customer) { S.srv = null; S.calcErr = ""; return; }
    S.timer = setTimeout(syncServer, 180);
  }
  async function syncServer() {
    const v = S.version;
    try {
      const r = await App.api.post(API + "calculate/", payload());
      if (v !== S.version) return;
      S.srv = r; S.srvFresh = true; S.calcErr = "";
      // pick up server-resolved unit prices (customer price group, promotion...) for display
      r.lines.forEach((l, i) => { if (S.cart[i] && S.cart[i].custom_price == null) S.cart[i].price = num(l.unit_price); });
      render();
    } catch (e) {
      if (v !== S.version || e.name === "AbortError") return;
      S.calcErr = e.code === "network" ? "" : e.message; render();
    }
  }
  async function ensureFresh() { if (S.srvFresh) return true; clearTimeout(S.timer); await syncServer(); return S.srvFresh; }

  /* ---------------------------------------------------------------- products */
  const tileHtml = (p, i) => {
    const out = p.track_stock && num(p.stock) <= 0;
    const stock = p.track_stock ? `<span class="p-stock ${out ? "out" : num(p.stock) <= 5 ? "low" : ""}">${out ? "Out" : App.qty(p.stock)}</span>` : "";
    const img = S.cfg.show_images && p.image ? `<img src="${App.esc(p.image)}" alt="" loading="lazy">` : `<span class="initials">${App.esc(App.initials(p.name))}</span>`;
    return `<button type="button" class="p-tile ${out && !S.cfg.overselling ? "disabled" : ""}" data-i="${i}" aria-label="${App.esc(p.name)}, ${App.money(p.price)}">${p.on_promo ? '<span class="p-promo">Sale</span>' : ""}${stock}<div class="p-img">${img}</div><div class="p-body"><div class="p-name">${App.esc(p.name)}</div><div class="p-price">${App.money(p.price)}</div></div></button>`;
  };
  async function loadProducts(reset) {
    if (reset) { S.offset = 0; S.products = []; }
    if (S.gridCtrl) S.gridCtrl.abort();
    const ctrl = (S.gridCtrl = new AbortController());
    S.loading = true; $("grid-state").innerHTML = ""; $("grid-more").classList.add("d-none");
    if (reset) $("grid").innerHTML = Array.from({ length: 12 }, () => '<div class="p-tile" style="pointer-events:none"><div class="skeleton" style="height:78px;border-radius:0"></div><div class="p-body"><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line w-50"></div></div></div>').join("");
    try {
      const d = await App.api.get(API + "products/", { q: S.q, category: S.category, customer: S.customer && S.customer.id, warehouse: S.warehouse, limit: S.cfg.products_per_page, offset: S.offset }, { signal: ctrl.signal });
      if (ctrl !== S.gridCtrl) return;
      const start = S.products.length; S.products = S.products.concat(d.results); S.total = d.count; S.offset = S.products.length;
      if (reset) $("grid").innerHTML = "";
      $("grid").insertAdjacentHTML("beforeend", d.results.map((p, i) => tileHtml(p, start + i)).join(""));
      if (!S.products.length) $("grid-state").innerHTML = App.emptyState({ icon: "search", title: S.q ? `No products match “${S.q}”` : "No products here", text: S.q ? "Check the spelling or scan the barcode." : "Choose another category." });
      $("grid-more").classList.toggle("d-none", S.products.length >= S.total);
      return d;
    } catch (e) {
      if (e.name === "AbortError") return;
      if (reset) $("grid").innerHTML = "";
      $("grid-state").innerHTML = App.errorState(e); $("grid-state").querySelector("[data-retry]")?.addEventListener("click", () => loadProducts(true));
    } finally { if (ctrl === S.gridCtrl) S.loading = false; }
  }

  function renderCats() {
    const roots = S.categories.filter((c) => !c.parent);
    $("cats").innerHTML = `<button class="chip ${S.category == null ? "active" : ""}" role="tab" aria-selected="${S.category == null}" data-cat="">All items</button>` + roots.map((c) => `<button class="chip ${S.category === c.id ? "active" : ""}" role="tab" aria-selected="${S.category === c.id}" data-cat="${c.id}">${App.esc(c.name)}</button>`).join("");
  }

  /* suggestions */
  function renderSuggest() {
    const box = $("suggest");
    if (!S.q || !S.sugItems.length) { box.classList.add("d-none"); box.innerHTML = ""; return; }
    box.classList.remove("d-none");
    box.innerHTML = S.sugItems.map((p, i) => `<div class="suggest-item ${i === S.sugIndex ? "active" : ""}" role="option" data-s="${i}" aria-selected="${i === S.sugIndex}"><div class="min-w-0"><div class="fw-medium text-truncate">${App.esc(p.name)}</div><div class="cell-sub">${App.esc(p.code)}${p.track_stock ? ` · ${num(p.stock) <= 0 ? '<span class="text-danger">out of stock</span>' : App.qty(p.stock) + " in stock"}` : ""}</div></div><strong class="tabular">${App.money(p.price)}</strong></div>`).join("");
  }
  async function fetchSuggestions(q) {
    if (S.sugCtrl) S.sugCtrl.abort();
    if (!q) { S.sugItems = []; renderSuggest(); return; }
    const ctrl = (S.sugCtrl = new AbortController());
    try {
      const d = await App.api.get(API + "products/", { q, customer: S.customer && S.customer.id, warehouse: S.warehouse, limit: 6 }, { signal: ctrl.signal });
      if (ctrl !== S.sugCtrl) return;
      S.sugItems = d.results; S.sugIndex = d.results.length && d.results[0].code.toLowerCase() === q.toLowerCase() ? 0 : -1; renderSuggest();
    } catch (e) { /* suggestions are best-effort */ }
  }
  const onSearch = App.debounce((q) => { S.q = q; fetchSuggestions(q); loadProducts(true); }, 220);

  async function submitSearch(scanned) {
    const input = $("search"), q = input.value.trim();
    if (!q) return;
    if (!scanned && S.sugIndex >= 0 && S.sugItems[S.sugIndex]) { addProduct(S.sugItems[S.sugIndex]); resetSearch(); return; }
    try {
      const d = await App.api.get(API + "products/", { barcode: q, customer: S.customer && S.customer.id, warehouse: S.warehouse });
      const hit = d.results[0], mv = hit && hit.matched_variant ? hit.variants.find((v) => v.id === hit.matched_variant) : undefined;
      if (hit && addProduct(hit, 1, mv)) { input.classList.add("scan-flash"); setTimeout(() => input.classList.remove("scan-flash"), 500); resetSearch(); }
    } catch (e) {
      if (e.status === 404) {
        if (S.sugItems.length === 1) { addProduct(S.sugItems[0]); resetSearch(); return; }
        App.toast("warning", "No product found", `Nothing matches “${q}”.`); input.select();
      } else App.toastError(e);
    }
  }
  function resetSearch() { const i = $("search"); i.value = ""; S.q = ""; S.sugItems = []; renderSuggest(); loadProducts(true); i.focus(); }

  /* ---------------------------------------------------------------- cart */
  function addProduct(p, qty = 1, variant) {
    if (!S.customer) { App.toast("warning", "Choose a customer first"); openCustomer(); return false; }
    if (p.has_variants && !variant && p.variants && p.variants.length) { pickVariant(p); return false; }
    const line = S.cart.find((l) => l.product_id === p.id && (l.variant_id || null) === (variant ? variant.id : null));
    const next = (line ? line.qty : 0) + qty;
    const avail = variant && variant.stock != null ? variant.stock : p.stock; // a variant has its own stock
    if (p.track_stock && !S.cfg.overselling && next > num(avail)) { App.toast("warning", num(avail) <= 0 ? "Out of stock" : "Not enough stock", `${p.name}${variant ? " · " + variant.name : ""}: ${App.qty(avail)} available.`); return false; }
    if (line) { line.qty = next; line.stock = avail; }
    else S.cart.push({ uid: ++uidSeq, product_id: p.id, variant_id: variant ? variant.id : null, variant_name: variant ? variant.name : "", code: p.code, name: p.name, unit: p.unit, price: num(variant && variant.price != null ? variant.price : p.price), tax_method: p.tax_method, tax_rate: p.tax_rate, qty, discount: "", custom_price: null, stock: avail, track: p.track_stock, image: p.image });
    changed();
    const el = document.querySelector(`[data-line="${(line || S.cart[S.cart.length - 1]).uid}"]`); if (el) el.scrollIntoView({ block: "nearest" });
    return true;
  }
  function pickVariant(p) {
    const d = App.dialog({ title: App.esc(p.name), size: "modal-sm", body: `<p class="text-muted">Choose a variant</p><div class="d-grid gap-2">${p.variants.map((v) => `<button type="button" class="btn btn-outline-secondary" data-v="${v.id}" ${v.stock != null && Number(v.stock) <= 0 && !S.cfg.overselling ? "disabled" : ""}><span>${App.esc(v.name)}</span>${v.stock != null ? ` <small class="text-muted">· ${App.qty(v.stock)} in stock</small>` : ""}${Number(v.price) !== Number(p.price) ? ` <small class="fw-semibold">· ${App.money(v.price)}</small>` : ""}</button>`).join("")}</div>` });
    d.el.addEventListener("click", (e) => { const b = e.target.closest("[data-v]"); if (!b) return; const v = p.variants.find((x) => x.id === Number(b.dataset.v)); d.close(); addProduct(p, 1, v); });
  }
  function setQty(uid, qty) {
    const l = S.cart.find((x) => x.uid === uid); if (!l) return;
    qty = Math.max(0, App.round(qty, 4));
    if (qty <= 0) { removeLine(uid); return; }
    if (l.track && !S.cfg.overselling && qty > num(l.stock)) { App.toast("warning", "Not enough stock", `${l.name}: ${App.qty(l.stock)} available.`); qty = num(l.stock); }
    l.qty = qty; changed();
  }
  function removeLine(uid) { S.cart = S.cart.filter((l) => l.uid !== uid); changed(); }
  async function clearCart(ask = true) {
    if (!S.cart.length) return;
    if (ask && !(await App.confirm({ title: "Clear this sale?", message: `All ${S.cart.length} item${S.cart.length > 1 ? "s" : ""} will be removed from the cart.`, confirmText: "Clear cart" }))) return;
    S.cart = []; S.orderDiscount = ""; S.orderTaxId = S.cfg.order_tax_enabled ? S.orderTaxId : null; S.shipping = 0; S.idem = App.uuid(); changed(); $("search").focus();
  }

  function render() {
    const t = T(), n = S.cart.length;
    $("cart-count").textContent = n; $("fab-count").textContent = App.qty(t.total_items || 0); $("fab-total").textContent = App.money(t.payable);
    if (!n) $("cart-lines").innerHTML = `<div class="cart-empty"><div><i class="bi bi-cart3"></i><h2 class="h6 mt-3">Cart is empty</h2><p class="mb-0">Scan a barcode or tap a product to start a sale.</p></div></div>`;
    else $("cart-lines").innerHTML = S.cart.map((l, i) => {
      const r = t.lines[i] || {}, unit = r.unit_price != null ? num(r.unit_price) : l.price;
      return `<div class="cart-line" data-line="${l.uid}">
        <div class="min-w-0"><div class="l-name text-truncate" title="${App.esc(l.name)}">${App.esc(l.name)}${l.variant_name ? ` <span class="text-muted">· ${App.esc(l.variant_name)}</span>` : ""}</div>
          <div class="l-meta"><button type="button" class="sum-link" data-edit="${l.uid}">${App.money(unit)}${l.custom_price != null ? " (edited)" : ""} × ${App.qty(l.qty)}${l.unit ? " " + App.esc(l.unit) : ""}</button>${l.discount ? ` · <span class="text-success">−${App.esc(l.discount)}</span>` : ""}${num(r.tax) ? ` · tax ${App.money(r.tax)}` : ""}</div></div>
        <div class="l-total">${App.money(r.subtotal != null ? r.subtotal : 0)}</div>
        <div class="qty" role="group" aria-label="Quantity for ${App.esc(l.name)}"><button type="button" data-dec="${l.uid}" aria-label="Decrease quantity"><i class="bi bi-dash-lg"></i></button><input type="text" inputmode="decimal" value="${App.qty(l.qty).replace(/,/g, "")}" data-qty="${l.uid}" aria-label="Quantity"><button type="button" data-inc="${l.uid}" aria-label="Increase quantity"><i class="bi bi-plus-lg"></i></button></div>
        <button type="button" class="btn btn-sm btn-soft-danger btn-icon ms-auto" data-del="${l.uid}" aria-label="Remove ${App.esc(l.name)}"><i class="bi bi-trash3"></i></button></div>`;
    }).join("");
    $("s-sub").textContent = App.money(t.total); $("s-disc").textContent = num(t.order_discount) ? "−" + App.money(t.order_discount) : App.money(0);
    $("s-tax").textContent = App.money(t.total_tax);
    $("row-ship").classList.toggle("d-none", !num(t.shipping)); $("s-ship").textContent = App.money(t.shipping);
    $("row-round").classList.toggle("d-none", !num(t.rounding)); $("s-round").textContent = App.money(t.rounding);
    $("s-total").textContent = App.money(t.payable);
    const ex = S.exchange, credit = ex ? num(ex.credit) : 0, toPay = Math.max(0, r4(num(t.payable) - credit));
    $("pay-amount").textContent = n ? App.money(ex ? toPay : t.payable) : "";
    $("btn-pay").firstElementChild.className = ex ? "bi bi-arrow-left-right" : "bi bi-credit-card-2-front";
    $("pay-label").textContent = ex ? "Exchange · pay" : "Pay";
    const bar = $("exchange-bar"); bar.classList.toggle("d-none", !ex);
    if (ex) {
      const left = n ? (num(t.payable) >= credit ? `Customer pays <strong>${App.money(toPay)}</strong>` : `Hand back <strong>${App.money(r4(credit - num(t.payable)))}</strong>`) : "Add the replacement items";
      bar.innerHTML = `<i class="bi bi-arrow-left-right ex-icon" aria-hidden="true"></i><div class="ex-main"><strong>Exchange for ${App.esc(ex.reference)}</strong><span class="text-muted">Credit from returned goods ${App.money(credit)} · ${left}</span></div><button type="button" class="btn btn-sm btn-outline-secondary" id="ex-cancel">Cancel</button>`;
      bar.querySelector("#ex-cancel").addEventListener("click", cancelExchange);
    }
    const err = $("calc-error"); err.textContent = S.calcErr; err.classList.toggle("d-none", !S.calcErr);
    $("btn-pay").disabled = !n || !!S.calcErr || S.paying;
    $("btn-customer").classList.toggle("locked", !!ex);
    $("btn-hold").disabled = !n || !S.cfg.allow_hold || !!ex; $("btn-clear").disabled = !n;
    $("btn-discount").classList.toggle("d-none", !(S.cfg.can_discount || (S.cfg.order_tax_enabled && S.cfg.tax_rates.length)));
  }

  /* cart interactions (event delegation) */
  $("cart-lines").addEventListener("click", (e) => {
    const t = e.target.closest("[data-inc],[data-dec],[data-del],[data-edit]"); if (!t) return;
    if (t.dataset.inc) { const l = S.cart.find((x) => x.uid == t.dataset.inc); setQty(l.uid, l.qty + 1); }
    else if (t.dataset.dec) { const l = S.cart.find((x) => x.uid == t.dataset.dec); if (l.qty > 1) setQty(l.uid, l.qty - 1); else setQty(l.uid, 1); }
    else if (t.dataset.del) removeLine(Number(t.dataset.del));
    else if (t.dataset.edit) editLine(Number(t.dataset.edit));
  });
  $("cart-lines").addEventListener("change", (e) => { if (e.target.dataset.qty) { const v = num(e.target.value); setQty(Number(e.target.dataset.qty), v > 0 ? v : 1); } });
  $("cart-lines").addEventListener("focusin", (e) => { if (e.target.dataset.qty) e.target.select(); });

  function editLine(uid) {
    const l = S.cart.find((x) => x.uid === uid); if (!l) return;
    const canP = S.cfg.can_edit_price, canD = S.cfg.can_discount;
    const d = App.dialog({
      title: App.esc(l.name), size: "modal-sm", scroll: false,
      body: `<form id="line-form" novalidate><div class="mb-3"><label class="form-label" for="le-qty">Quantity</label><input class="form-control" id="le-qty" inputmode="decimal" value="${l.qty}"></div>
        ${canP ? `<div class="mb-3"><label class="form-label" for="le-price">Unit price</label><input class="form-control" id="le-price" inputmode="decimal" value="${l.custom_price != null ? l.custom_price : l.price}"><div class="form-text">Overrides the catalogue price for this sale only.</div></div>` : ""}
        ${canD ? `<div class="mb-1"><label class="form-label" for="le-disc">Discount</label><input class="form-control" id="le-disc" value="${App.esc(l.discount)}" placeholder="10% or 50"><div class="form-text">Percentage (10%) or amount per unit.</div></div>` : ""}</form>`,
      footer: `<button class="btn btn-soft-danger me-auto" data-del><i class="bi bi-trash3"></i>Remove</button><button class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button><button class="btn btn-primary" data-ok>Apply</button>`,
      onShown: (el) => el.querySelector("#le-qty").select(),
    });
    const apply = () => {
      const q = num(d.el.querySelector("#le-qty").value); if (q <= 0) return App.toast("warning", "Quantity must be above zero");
      if (canP) { const pv = d.el.querySelector("#le-price").value.trim(); l.custom_price = pv === "" || num(pv) === l.price ? null : num(pv); }
      if (canD) l.discount = d.el.querySelector("#le-disc").value.trim();
      d.close(); setQty(uid, q); changed();
    };
    d.el.querySelector("[data-ok]").addEventListener("click", apply);
    d.el.querySelector("#line-form").addEventListener("submit", (e) => { e.preventDefault(); apply(); });
    d.el.querySelector("[data-del]").addEventListener("click", () => { d.close(); removeLine(uid); });
  }

  /* ---------------------------------------------------------------- order discount / tax / shipping */
  function openAdjust() {
    const canD = S.cfg.can_discount, canT = S.cfg.order_tax_enabled && S.cfg.tax_rates.length;
    const d = App.dialog({
      title: "Discount, tax & shipping", size: "modal-sm", scroll: false,
      body: `<form id="adj-form" novalidate>${canD ? `<div class="mb-3"><label class="form-label" for="ad-disc">Order discount</label><input class="form-control" id="ad-disc" value="${App.esc(S.orderDiscount)}" placeholder="10% or 100"><div class="form-text">Applied to the whole sale, after product tax.</div></div>` : ""}
        ${canT ? `<div class="mb-3"><label class="form-label" for="ad-tax">Order tax</label><select class="form-select" id="ad-tax"><option value="">No order tax</option>${S.cfg.tax_rates.map((t) => `<option value="${t.id}" ${S.orderTaxId === t.id ? "selected" : ""}>${App.esc(t.name)}</option>`).join("")}</select></div>` : ""}
        <div class="mb-1"><label class="form-label" for="ad-ship">Shipping</label><input class="form-control" id="ad-ship" inputmode="decimal" value="${num(S.shipping) || ""}" placeholder="0"></div></form>`,
      footer: `<button class="btn btn-outline-secondary me-auto" data-reset>Reset</button><button class="btn btn-primary" data-ok>Apply</button>`,
      onShown: (el) => (el.querySelector("input,select") || {}).focus && el.querySelector("input,select").focus(),
    });
    const ok = () => { if (canD) S.orderDiscount = d.el.querySelector("#ad-disc").value.trim(); if (canT) S.orderTaxId = d.el.querySelector("#ad-tax").value ? Number(d.el.querySelector("#ad-tax").value) : null; S.shipping = num(d.el.querySelector("#ad-ship").value); d.close(); changed(); };
    d.el.querySelector("[data-ok]").addEventListener("click", ok);
    d.el.querySelector("#adj-form").addEventListener("submit", (e) => { e.preventDefault(); ok(); });
    d.el.querySelector("[data-reset]").addEventListener("click", () => { S.orderDiscount = ""; S.orderTaxId = null; S.shipping = 0; d.close(); changed(); });
  }

  /* ---------------------------------------------------------------- customer */
  function setCustomer(c) {
    const changedCustomer = !S.customer || S.customer.id !== c.id;
    S.customer = { id: c.id, name: c.name, phone: c.phone || "", group: c.group || "", points: c.points || 0, deposit: c.deposit || 0, is_walk_in: !!c.is_walk_in };
    $("cust-name").textContent = c.name; $("cust-avatar").textContent = App.initials(c.name);
    const bits = [c.phone, c.group, c.points ? `${c.points} pts` : "", num(c.deposit) ? `deposit ${App.money(c.deposit)}` : ""].filter(Boolean);
    $("cust-meta").textContent = bits.length ? bits.join(" · ") : "Tap to change customer";
    if (changedCustomer) { if (S.products.length) loadProducts(true); if (S.cart.length) changed(); }
  }
  function openCustomer() {
    if (S.exchange) { App.toast("info", "Customer is fixed for an exchange", `This exchange is with ${S.exchange.customer_name}. Cancel the exchange to change customer.`); return; }
    const d = App.dialog({
      title: "Select customer", size: "", scroll: true,
      body: `<div class="input-icon mb-3"><i class="bi bi-search"></i><input class="form-control" id="cu-q" placeholder="Search by name, phone or email" autocomplete="off" aria-label="Search customers"></div><div id="cu-list"></div>
        <div class="mt-3"><button class="btn btn-soft w-100" id="cu-new-toggle" type="button"><i class="bi bi-person-plus"></i>New customer</button>
        <form id="cu-new" class="d-none mt-3" novalidate><div class="row g-2"><div class="col-12 col-sm-6"><label class="form-label" for="cn-name">Name <span class="req">*</span></label><input class="form-control" id="cn-name" name="name" required></div><div class="col-12 col-sm-6"><label class="form-label" for="cn-phone">Phone</label><input class="form-control" id="cn-phone" name="phone" inputmode="tel"></div><div class="col-12"><button class="btn btn-primary w-100" type="submit">Save & select</button></div></div></form></div>`,
      onShown: (el) => el.querySelector("#cu-q").focus(),
    });
    const list = d.el.querySelector("#cu-list");
    const show = async (q) => {
      list.innerHTML = '<div class="skeleton skeleton-line"></div>'.repeat(3);
      try {
        const rows = await App.api.get(API + "customers/", { q });
        list.innerHTML = rows.length ? rows.map((c) => `<button type="button" class="list-row w-100 border-0 bg-transparent text-start" data-c='${App.esc(JSON.stringify(c))}'><span class="avatar">${App.esc(App.initials(c.name))}</span><span class="flex-grow-1 min-w-0"><span class="d-block cell-main text-truncate">${App.esc(c.name)}</span><span class="cell-sub">${[c.phone, c.group].filter(Boolean).map(App.esc).join(" · ") || "&nbsp;"}</span></span>${num(c.deposit) ? `<span class="badge badge-soft-info">Deposit ${App.money(c.deposit)}</span>` : ""}${c.points ? `<span class="badge badge-soft-primary">${c.points} pts</span>` : ""}</button>`).join("") : App.emptyState({ icon: "person-x", title: "No customers found", text: "Try another name or add a new customer." });
      } catch (e) { list.innerHTML = App.errorState(e); list.querySelector("[data-retry]")?.addEventListener("click", () => show(q)); }
    };
    show("");
    d.el.querySelector("#cu-q").addEventListener("input", App.debounce((e) => show(e.target.value.trim()), 250));
    list.addEventListener("click", (e) => { const b = e.target.closest("[data-c]"); if (!b) return; setCustomer(JSON.parse(b.dataset.c)); d.close(); });
    d.el.querySelector("#cu-new-toggle").addEventListener("click", () => { d.el.querySelector("#cu-new").classList.toggle("d-none"); d.el.querySelector("#cn-name").focus(); });
    d.el.querySelector("#cu-new").addEventListener("submit", async (e) => {
      e.preventDefault(); const f = e.target; App.clearErrors(f);
      if (!f.name.value.trim()) { f.name.classList.add("is-invalid"); return; }
      await App.busy(f.querySelector("[type=submit]"), async () => {
        try { const c = await App.api.post("/api/v1/customers/", { name: f.name.value.trim(), phone: f.phone.value.trim() }); setCustomer({ id: c.id, name: c.company && c.company !== "-" ? c.company : c.name, phone: c.phone }); d.close(); App.toast("success", "Customer added"); }
        catch (err) { App.showErrors(f, err); }
      });
    });
  }

  /* ---------------------------------------------------------------- payment */
  function openPayment(preset) {
    if (!S.cart.length || S.paying) return;
    if (!S.customer) { openCustomer(); return; }
    (async () => {
      if (!(await ensureFresh())) { if (S.calcErr) App.toast("danger", "Cannot take payment", S.calcErr); return; }
      const t = S.srv, ex = S.exchange, credit = ex ? num(ex.credit) : 0, fullPayable = num(t.payable), payable = Math.max(0, r4(fullPayable - credit)), handBack = Math.max(0, r4(credit - fullPayable)); let seq = 0, backMethod = "cash";
      const rows = [{ id: ++seq, method: preset || "cash", amount: payable, gift: "", cheque: "", last4: "", ref: "" }];
      if (ex && !payable) rows.length = 0, rows.push({ id: ++seq, method: "cash", amount: 0, gift: "", cheque: "", last4: "", ref: "" });
      const methods = S.cfg.payment_methods;
      const d = App.dialog({
        title: ex ? "Exchange payment" : "Payment", size: "modal-lg", static: true,
        body: `<div class="row g-4"><div class="col-12 col-lg-7">
            <div class="form-section-title">Payment method</div><div class="pay-methods mb-3" id="pm">${methods.map((m) => `<button type="button" class="pay-method" data-m="${m.value}"><i class="bi bi-${ICON[m.value] || "cash"}" aria-hidden="true"></i>${App.esc(m.label)}</button>`).join("")}</div>
            <div id="pay-rows"></div><button type="button" class="btn btn-sm btn-soft mt-2" id="add-split"><i class="bi bi-plus-lg"></i>Split payment</button><div id="pay-error" class="mt-3"></div></div>
          <div class="col-12 col-lg-5"><div class="form-section"><div class="text-muted small">Amount to pay</div><div class="display-6 fw-bold tabular mb-3">${App.money(payable)}</div>
            ${ex ? `<div class="sum-row"><span>New items</span><span>${App.money(fullPayable)}</span></div><div class="sum-row"><span>Exchange credit</span><span>−${App.money(credit)}</span></div>` : ""}
            ${handBack ? `<div class="alert alert-info py-2 my-2"><div class="small">Hand back to the customer</div><div class="fw-bold fs-5 tabular">${App.money(handBack)}</div><label class="visually-hidden" for="ex-back">Refund method</label><select class="form-select form-select-sm mt-1" id="ex-back"><option value="cash">Cash</option><option value="card">Card</option><option value="bank">Bank</option>${S.customer && !S.customer.is_walk_in ? '<option value="deposit">Customer deposit</option>' : ""}<option value="other">Other</option></select></div>` : ""}
            <div class="sum-row"><span>Items</span><span>${App.qty(t.total_items)}</span></div><div class="sum-row"><span>Customer</span><span class="text-truncate ms-3">${App.esc(S.customer.name)}</span></div>
            <hr><div class="sum-row"><span>Tendered</span><span id="pt-tendered">0.00</span></div><div id="pt-result" class="change-box ok mt-2"></div>
            <div class="small text-muted mt-3">Tip: press <kbd>Enter</kbd> to complete the sale.</div></div></div></div>`,
        footer: `<button class="btn btn-outline-secondary" data-bs-dismiss="modal">Back to cart</button><button class="btn btn-success btn-lg" id="pay-done"><i class="bi bi-check2-circle"></i>${ex ? "Complete exchange" : "Complete sale"}</button>`,
      });
      const $$ = (s) => d.el.querySelector(s);
      let active = 1;
      const sums = () => { const tendered = r4(rows.reduce((a, r) => a + num(r.amount), 0)); const cash = rows.filter((r) => r.method === "cash").reduce((a, r) => a + num(r.amount), 0); const change = Math.max(0, r4(tendered - payable)); return { tendered, cash, change, due: Math.max(0, r4(payable - tendered)) }; };
      const renderRows = () => {
        $$("#pay-rows").innerHTML = rows.map((r) => {
          const due = Math.max(0, r4(payable - rows.filter((x) => x.id !== r.id).reduce((a, x) => a + num(x.amount), 0)));
          const meta = methods.find((m) => m.value === r.method) || {}, req = meta.require_ref ? " (required)" : "";
          const extra0 = r.method === "gift_card" ? `<div class="input-group input-group-sm mt-2"><input class="form-control" data-f="gift" placeholder="Gift card number" value="${App.esc(r.gift)}" aria-label="Gift card number"><button class="btn btn-outline-secondary" type="button" data-check="${r.id}">Check</button></div><div class="small mt-1" data-gc="${r.id}"></div>`
            : r.method === "cheque" ? `<input class="form-control form-control-sm mt-2" data-f="cheque" placeholder="Cheque number${req}" value="${App.esc(r.cheque)}" aria-label="Cheque number">`
            : r.method === "card" ? `<div class="row g-2 mt-0"><div class="col-6"><input class="form-control form-control-sm" data-f="last4" maxlength="4" inputmode="numeric" placeholder="Last 4 digits" value="${App.esc(r.last4)}" aria-label="Card last four digits"></div><div class="col-6"><input class="form-control form-control-sm" data-f="ref" placeholder="Approval / ref${req}" value="${App.esc(r.ref)}" aria-label="Approval reference"></div></div>`
            : r.method === "deposit" ? `<div class="small text-muted mt-1">Customer deposit available: <strong>${App.money(S.customer.deposit || 0)}</strong></div>` : "";
          const refPh = { bank: "Transfer / transaction ID", wallet: "Wallet transaction ID", other: "Reference" }[r.method];
          const extra = extra0 + (refPh ? `<input class="form-control form-control-sm mt-2" data-f="ref" placeholder="${refPh}${req}" value="${App.esc(r.ref)}" aria-label="${refPh}">` : "") + (meta.instructions ? `<div class="pay-note mt-2"><i class="bi bi-info-circle" aria-hidden="true"></i><span>${App.esc(meta.instructions)}</span></div>` : "");
          const quick = r.method === "cash" ? `<div class="quick-cash mt-2">${cashSuggestions(due).map((v) => `<button type="button" class="btn btn-sm btn-outline-secondary" data-q="${r.id}:${v}">${v === due ? "Exact" : App.num(v, 0)}</button>`).join("")}</div>` : "";
          return `<div class="form-section mb-2 ${r.id === active ? "border-primary" : ""}" data-row="${r.id}"><div class="d-flex gap-2 align-items-center"><i class="bi bi-${ICON[r.method] || "cash"} fs-5 text-primary"></i><select class="form-select form-select-sm" data-f="method" aria-label="Payment method">${methods.map((m) => `<option value="${m.value}" ${m.value === r.method ? "selected" : ""}>${App.esc(m.label)}</option>`).join("")}</select><input class="form-control form-control-sm text-end fw-semibold" data-f="amount" inputmode="decimal" value="${r.amount || ""}" aria-label="Amount" style="max-width:150px">${rows.length > 1 ? `<button type="button" class="btn btn-sm btn-soft-danger btn-icon" data-rm="${r.id}" aria-label="Remove payment"><i class="bi bi-x-lg"></i></button>` : ""}</div>${extra}${quick}</div>`;
        }).join("");
        $$("#pm").querySelectorAll(".pay-method").forEach((b) => b.classList.toggle("active", (rows.find((r) => r.id === active) || {}).method === b.dataset.m));
        summary();
      };
      const summary = () => {
        const s = sums(); $$("#pt-tendered").textContent = App.money(s.tendered);
        const res = $$("#pt-result"), btn = $$("#pay-done"); let ok = true, msg = "";
        if (s.tendered > payable) { if (s.cash < s.change) { ok = false; msg = "Only cash can be over-tendered."; } res.className = "change-box ok mt-2"; res.innerHTML = `<span>Change to give</span><span class="fs-4">${App.money(s.change)}</span>`; }
        else if (s.due > 0) { res.className = "change-box due mt-2"; res.innerHTML = `<span>${S.cfg.allow_credit_sale ? "Left on account" : "Still to pay"}</span><span class="fs-4">${App.money(s.due)}</span>`; if (!S.cfg.allow_credit_sale) { ok = false; } }
        else { res.className = "change-box ok mt-2"; res.innerHTML = `<span>Paid in full</span><i class="bi bi-check-circle-fill fs-4"></i>`; }
        if (!rows.every((r) => num(r.amount) >= 0)) ok = false;
        btn.disabled = !ok || (s.tendered <= 0 && payable > 0 && !S.cfg.allow_credit_sale); $$("#pay-error").innerHTML = msg ? `<div class="alert alert-warning py-2 mb-0">${msg}</div>` : $$("#pay-error").querySelector(".alert-danger") ? $$("#pay-error").innerHTML : "";
      };
      const rowOf = (el) => rows.find((r) => r.id === Number(el.closest("[data-row]").dataset.row));
      $$("#pay-rows").addEventListener("input", (e) => { const f = e.target.dataset.f; if (!f || f === "method") return; const r = rowOf(e.target); if (f === "amount") { r.amount = e.target.value.replace(/[^0-9.]/g, ""); } else r[f] = e.target.value; if (f === "amount") summary(); });
      $$("#pay-rows").addEventListener("change", (e) => { if (e.target.dataset.f === "method") { rowOf(e.target).method = e.target.value; active = rowOf(e.target).id; renderRows(); } });
      $$("#pay-rows").addEventListener("focusin", (e) => { const r = e.target.closest("[data-row]"); if (r && Number(r.dataset.row) !== active) { active = Number(r.dataset.row); $$("#pm").querySelectorAll(".pay-method").forEach((b) => b.classList.toggle("active", rows.find((x) => x.id === active).method === b.dataset.m)); } if (e.target.dataset.f === "amount") e.target.select(); });
      $$("#pay-rows").addEventListener("click", async (e) => {
        const q = e.target.closest("[data-q]"), rm = e.target.closest("[data-rm]"), chk = e.target.closest("[data-check]");
        if (q) { const [id, v] = q.dataset.q.split(":"); rows.find((r) => r.id === Number(id)).amount = Number(v); renderRows(); }
        if (rm) { rows.splice(rows.findIndex((r) => r.id === Number(rm.dataset.rm)), 1); active = rows[0].id; renderRows(); }
        if (chk) {
          const r = rows.find((x) => x.id === Number(chk.dataset.check)), out = $$(`[data-gc="${r.id}"]`);
          try { const g = await App.api.get(API + "gift-card/", { card_no: r.gift }); out.innerHTML = g.valid ? `<span class="text-success">Balance ${App.money(g.balance)}</span>` : '<span class="text-danger">Card is empty or expired</span>'; if (g.valid && num(r.amount) > num(g.balance)) { r.amount = num(g.balance); renderRows(); } }
          catch (err) { out.innerHTML = `<span class="text-danger">${App.esc(err.message)}</span>`; }
        }
      });
      $$("#pm").addEventListener("click", (e) => { const b = e.target.closest("[data-m]"); if (!b) return; const r = rows.find((x) => x.id === active) || rows[0]; r.method = b.dataset.m; renderRows(); });
      $$("#add-split").addEventListener("click", () => { const left = Math.max(0, r4(payable - rows.reduce((a, r) => a + num(r.amount), 0))); rows.push({ id: ++seq, method: (methods.find((m) => m.value !== "cash") || methods[0]).value, amount: left, gift: "", cheque: "", last4: "", ref: "" }); active = seq; renderRows(); });
      const finish = async () => {
        const btn = $$("#pay-done"); if (btn.disabled) return;
        const lacking = rows.find((r) => num(r.amount) > 0 && (methods.find((m) => m.value === r.method) || {}).require_ref && !String(r.ref || r.cheque || "").trim());
        if (lacking) {
          const m = methods.find((x) => x.value === lacking.method);
          $$("#pay-error").innerHTML = `<div class="alert alert-warning d-flex gap-2 mb-0" role="alert"><i class="bi bi-exclamation-triangle-fill"></i><div>Enter the reference number for ${App.esc(m.label)} before completing the sale.</div></div>`;
          const inp = $$(`[data-row="${lacking.id}"] [data-f="ref"], [data-row="${lacking.id}"] [data-f="cheque"]`); if (inp) inp.focus();
          return;
        }
        S.paying = true; render();
        await App.busy(btn, async () => {
          try {
            const body = payload({ payments: rows.filter((r) => num(r.amount) > 0).map((r) => ({ method: r.method, amount: String(num(r.amount)), gift_card_no: r.gift, cheque_no: r.cheque, card_last4: r.last4, transaction_ref: r.ref })) });
            let res;
            if (ex) {
              body.idempotency_key = S.idem;
              res = await App.api.post(`/api/v1/sales/${ex.sale_id}/exchange/`, { returns: ex.lines, restock: ex.restock, note: ex.note || "", refund_method: backMethod, new: body });
              S.exchange = null;
            } else res = await App.api.post(API + "checkout/", body, { headers: { "Idempotency-Key": S.idem } });
            d.close(); S.cart = []; S.orderDiscount = ""; S.shipping = 0; S.idem = App.uuid(); S.paying = false; changed();
            if (S.register && S.register.open) refreshRegister();
            loadProducts(true);
            $("cart-panel").classList.remove("open"); // on a phone / tablet the cart is an overlay: go back to the products
            showReceipt(res.receipt, res.created === false, res.sale);
          } catch (err) {
            S.paying = false; render();
            const box = $$("#pay-error"); box.innerHTML = `<div class="alert alert-danger d-flex gap-2 mb-0" role="alert"><i class="bi bi-exclamation-triangle-fill"></i><div>${App.esc(err.message)}${err.code === "network" ? "<br><small>Nothing was charged yet. It is safe to press Complete again.</small>" : ""}</div></div>`;
            if (err.code === "register_closed") { d.close(); openRegister(true); }
          }
        });
      };
      const back = $$("#ex-back"); if (back) back.addEventListener("change", () => { backMethod = back.value; });
      $$("#pay-done").addEventListener("click", finish);
      d.el.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.target.closest("button") && !e.target.matches("select")) { e.preventDefault(); finish(); } });
      d.el.addEventListener("hidden.bs.modal", () => $("search").focus());
      renderRows(); setTimeout(() => { const a = d.el.querySelector('[data-f="amount"]'); if (a) a.select(); }, 250);
    })();
  }
  function cashSuggestions(due) {
    const out = new Set([r4(due)]);
    [10, 50, 100, 500, 1000, 5000].forEach((step) => { const v = Math.ceil(due / step) * step; if (v > 0 && v >= due) out.add(v); });
    return Array.from(out).sort((a, b) => a - b).slice(0, 5);
  }

  /* ---------------------------------------------------------------- receipt */
  function showReceipt(r, replay, sale) {
    const saleId = sale && sale.id, html = App.receipt.html(r);
    const change = r.payments.reduce((a, p) => a + num(p.change), 0), due = num(r.balance);
    const d = App.dialog({
      title: '<i class="bi bi-check-circle-fill text-success me-2"></i>Sale complete', size: "dialog-compact", scroll: true,
      body: `${replay ? '<div class="alert alert-info py-2 mb-3">This sale had already been saved — nothing was charged twice.</div>' : ""}
        <div class="done-hero"><div class="done-ref">${App.esc(r.reference)}</div><div class="done-label">Total paid</div><div class="done-total tabular">${App.money(r.payable)}</div>
          <div class="done-chips">${change > 0 ? `<span class="done-chip ok"><i class="bi bi-cash-coin"></i>Change ${App.money(change)}</span>` : ""}${due > 0 ? `<span class="done-chip warn"><i class="bi bi-exclamation-circle"></i>Balance due ${App.money(due)}</span>` : ""}<span class="done-chip"><i class="bi bi-person"></i>${App.esc(r.customer.name)}</span></div></div>
        <div class="done-actions" role="group" aria-label="After the sale">
          <button type="button" class="done-act" id="rc-print"><i class="bi bi-printer"></i><span>Print</span></button>
          <button type="button" class="done-act" id="rc-email"><i class="bi bi-envelope"></i><span>Email</span></button>
          <button type="button" class="done-act" id="rc-wa"><i class="bi bi-whatsapp"></i><span>WhatsApp</span></button>
          <button type="button" class="done-act" id="rc-fb"><i class="bi bi-chat-heart"></i><span>Feedback</span></button>
        </div>
        <button type="button" class="btn btn-link btn-sm text-secondary d-block mx-auto mt-3" id="rc-toggle" aria-expanded="false" aria-controls="rc-paper"><i class="bi bi-receipt"></i> <span>View receipt</span></button>
        <div class="rc-paper d-none" id="rc-paper">${html}</div>`,
      footer: `<button class="btn btn-primary btn-lg w-100" id="rc-new" data-bs-dismiss="modal"><i class="bi bi-plus-lg"></i>New sale</button>`,
    });
    const $d = (sel) => d.el.querySelector(sel);
    $d("#rc-print").addEventListener("click", () => App.receipt.printSale(r, saleId));
    const noSale = () => { App.toast("warning", "Sale not available", "Open the sale from Sales to continue."); };
    $d("#rc-email").addEventListener("click", () => (saleId ? App.sendReceipt(sale, { channel: "email" }) : noSale()));
    $d("#rc-wa").addEventListener("click", () => (saleId ? App.sendReceipt(sale, { channel: "whatsapp" }) : noSale()));
    $d("#rc-fb").addEventListener("click", () => (saleId ? App.feedbackDialog(sale, { onSaved: (f) => { const b = $d("#rc-fb"); b.classList.add("done"); b.querySelector("span").textContent = `${f.rating}★ saved`; } }) : noSale()));
    $d("#rc-toggle").addEventListener("click", (e) => {
      const p = $d("#rc-paper"), open = p.classList.toggle("d-none") === false;
      e.currentTarget.setAttribute("aria-expanded", open); e.currentTarget.querySelector("span").textContent = open ? "Hide receipt" : "View receipt";
    });
    d.el.addEventListener("hidden.bs.modal", () => $("search").focus());
    d.el.addEventListener("shown.bs.modal", () => $d("#rc-new").focus());
    const dp = App.receipt.defaultPrinter();
    if (!replay && dp && dp.auto_print) App.receipt.printSale(r, saleId);
    S.lastReceipt = html;
  }

  /* ---------------------------------------------------------------- hold / resume */
  async function holdSale() {
    if (!S.cart.length || !S.cfg.allow_hold) return;
    const d = App.dialog({ title: "Hold this sale", size: "modal-sm", scroll: false, body: `<label class="form-label" for="h-name">Label (optional)</label><input class="form-control" id="h-name" placeholder="e.g. Table 4 / Mr. Ahmed" maxlength="60" value="${App.esc(S.customer && !S.customer.is_walk_in ? S.customer.name : "")}">`, footer: `<button class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button><button class="btn btn-primary" data-ok>Hold sale</button>`, onShown: (el) => el.querySelector("#h-name").focus() });
    const go = async () => {
      await App.busy(d.el.querySelector("[data-ok]"), async () => {
        try {
          const t = T();
          await App.api.post(API + "held/", { name: d.el.querySelector("#h-name").value.trim(), customer_id: S.customer.id, total: String(t.payable), item_count: S.cart.length, cart: { customer: S.customer, lines: S.cart.map((l) => ({ ...l })), order_discount: S.orderDiscount, order_tax_id: S.orderTaxId, shipping: S.shipping } });
          d.close(); S.cart = []; S.orderDiscount = ""; S.shipping = 0; S.idem = App.uuid(); changed(); App.toast("success", "Sale held", "Resume it any time from Held."); refreshHeld(); $("search").focus();
        } catch (e) { App.toastError(e, "Could not hold the sale"); }
      });
    };
    d.el.querySelector("[data-ok]").addEventListener("click", go);
    d.el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
  }
  async function refreshHeld() { try { const l = await App.api.get(API + "held/"); S.heldCount = l.length; const b = $("held-count"); b.textContent = l.length; b.classList.toggle("d-none", !l.length); return l; } catch (e) { return []; } }
  async function openHeld() {
    const d = App.dialog({ title: "Held sales", size: "", body: '<div id="hl">' + '<div class="skeleton skeleton-line"></div>'.repeat(3) + "</div>" });
    const box = d.el.querySelector("#hl");
    const draw = async () => {
      const l = await refreshHeld();
      box.innerHTML = l.length ? l.map((h) => `<div class="list-row"><div class="thumb"><i class="bi bi-pause-circle"></i></div><div class="flex-grow-1 min-w-0"><div class="cell-main text-truncate">${App.esc(h.name || h.customer_name || "Held sale")}</div><div class="cell-sub">${h.item_count} item${h.item_count === 1 ? "" : "s"} · ${App.datetime(h.created_at)} · ${App.esc(h.cashier)}</div></div><strong class="tabular">${App.money(h.total)}</strong><button class="btn btn-sm btn-primary" data-resume="${h.id}">Resume</button><button class="btn btn-sm btn-soft-danger btn-icon" data-drop="${h.id}" aria-label="Delete held sale"><i class="bi bi-trash3"></i></button></div>`).join("") : App.emptyState({ icon: "pause-circle", title: "No held sales", text: "Use Hold to park a sale and serve the next customer." });
    };
    draw();
    box.addEventListener("click", async (e) => {
      const r = e.target.closest("[data-resume]"), x = e.target.closest("[data-drop]");
      if (r) {
        if (S.cart.length && !(await App.confirm({ title: "Replace current cart?", message: "The sale in progress will be discarded.", confirmText: "Replace", tone: "warning" }))) return;
        try { const p = await App.api.post(`${API}held/${r.dataset.resume}/resume/`); restore(p); d.close(); } catch (err) { App.toastError(err); draw(); }
      }
      if (x && (await App.confirm({ title: "Delete held sale?", message: "This cannot be undone.", confirmText: "Delete" }))) { try { await App.api.delete(`${API}held/${x.dataset.drop}/`); draw(); } catch (err) { App.toastError(err); } }
    });
  }
  function restore(p) {
    if (p.customer) setCustomer(p.customer);
    S.cart = (p.lines || []).map((l) => ({ ...l, uid: ++uidSeq })); S.orderDiscount = p.order_discount || ""; S.orderTaxId = p.order_tax_id || null; S.shipping = p.shipping || 0; S.idem = App.uuid();
    changed(); App.toast("success", "Sale resumed");
  }

  /* ---------------------------------------------------------------- returns */
  function openReturns(openSaleId) {
    const d = App.dialog({
      title: "Return or exchange", size: "modal-lg",
      body: `<div class="input-icon mb-3"><i class="bi bi-search"></i><input class="form-control" id="rt-q" placeholder="Sale reference, customer name or phone" autocomplete="off" aria-label="Find sale"></div><div id="rt-body"></div>`,
      onShown: (el) => el.querySelector("#rt-q").focus(),
    });
    const body = d.el.querySelector("#rt-body");
    const find = async (q) => {
      body.innerHTML = '<div class="skeleton skeleton-line"></div>'.repeat(3);
      try {
        const rows = await App.api.get(API + "sales/", { q });
        body.innerHTML = rows.length ? rows.map((s) => `<button type="button" class="list-row w-100 border-0 bg-transparent text-start" data-sale="${s.id}"><div class="flex-grow-1 min-w-0"><div class="cell-main">${App.esc(s.reference)}</div><div class="cell-sub">${App.esc(s.customer_name)} · ${App.datetime(s.date)}</div></div><div class="text-end"><strong class="tabular">${App.money(s.grand_total)}</strong>${num(s.returned_total) ? `<div class="cell-sub">returned ${App.money(s.returned_total)}</div>` : ""}</div><i class="bi bi-chevron-right text-muted"></i></button>`).join("") : App.emptyState({ icon: "receipt", title: "No sales found", text: "Check the reference printed on the receipt." });
      } catch (e) { body.innerHTML = App.errorState(e); body.querySelector("[data-retry]")?.addEventListener("click", () => find(q)); }
    };
    if (openSaleId) { body.innerHTML = '<div class="skeleton skeleton-line"></div>'.repeat(4); App.api.get(`${API}sales/${openSaleId}/`).then((sl) => returnForm(d, body, sl)).catch((err) => { body.innerHTML = App.errorState(err); }); }
    else find("");
    d.el.querySelector("#rt-q").addEventListener("input", App.debounce((e) => find(e.target.value.trim()), 250));
    body.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-sale]"); if (!b) return;
      body.innerHTML = '<div class="skeleton skeleton-line"></div>'.repeat(4);
      try { returnForm(d, body, await App.api.get(`${API}sales/${b.dataset.sale}/`)); } catch (err) { body.innerHTML = App.errorState(err); }
    });
  }
  function returnForm(d, body, sale) {
    const items = sale.items.filter((i) => num(i.returnable) > 0);
    if (!items.length) { body.innerHTML = App.emptyState({ icon: "check2-all", title: "Nothing left to return", text: `Every item on ${sale.reference} has already been returned.` }); return; }
    body.innerHTML = `<div class="d-flex justify-content-between align-items-center mb-3"><div><div class="fw-semibold">${App.esc(sale.reference)}</div><div class="cell-sub">${App.esc(sale.customer_name)} · paid ${App.money(sale.paid)}</div></div><button class="btn btn-sm btn-outline-secondary" data-back><i class="bi bi-arrow-left"></i>Back</button></div>
      <form id="rt-form" novalidate><div class="table-wrap mb-3"><table class="table"><thead><tr><th>Item</th><th class="num">Price</th><th class="num">Sold</th><th class="num">Return qty</th></tr></thead><tbody>${items.map((i) => `<tr><td>${App.esc(i.product_name)}</td><td class="num">${App.money(i.unit_price)}</td><td class="num">${App.qty(i.quantity)}</td><td class="num"><input class="form-control form-control-sm text-end ms-auto" style="max-width:100px" inputmode="decimal" data-ri="${i.id}" data-max="${i.returnable}" value="0" aria-label="Return quantity for ${App.esc(i.product_name)}"></td></tr>`).join("")}</tbody></table></div>
      <div class="row g-3"><div class="col-sm-4"><label class="form-label" for="rt-method">Refund to</label><select class="form-select" id="rt-method"><option value="cash">Cash</option><option value="card">Card</option><option value="bank">Bank</option>${sale.customer ? '<option value="deposit">Customer deposit</option>' : ""}<option value="other">Other</option></select></div><div class="col-sm-4"><label class="form-label" for="rt-sur">Restocking fee</label><input class="form-control" id="rt-sur" inputmode="decimal" value="0"></div><div class="col-sm-4"><label class="form-label d-block">&nbsp;</label><div class="form-check form-switch mt-2"><input class="form-check-input" type="checkbox" id="rt-restock" checked><label class="form-check-label" for="rt-restock">Return to stock</label></div></div><div class="col-12"><label class="form-label" for="rt-note">Reason / note</label><input class="form-control" id="rt-note" maxlength="200"></div></div>
      <div id="rt-error" class="mt-3"></div><div class="d-flex justify-content-end gap-2 mt-3"><button class="btn btn-outline-secondary" type="button" data-bs-dismiss="modal">Cancel</button>${S.cfg.can_return && sale.status === "completed" ? '<button class="btn btn-outline-primary" type="button" id="rt-exchange"><i class="bi bi-arrow-left-right"></i>Exchange for other items</button>' : ""}<button class="btn btn-danger" type="submit"><i class="bi bi-arrow-return-left"></i>Refund only</button></div></form>`;
    body.querySelector("[data-back]").addEventListener("click", () => { d.close(); openReturns(); });
    const picked = () => {
      const lines = Array.from(body.querySelectorAll("[data-ri]")).map((i) => ({ sale_item_id: Number(i.dataset.ri), quantity: num(i.value), max: num(i.dataset.max), el: i })).filter((l) => l.quantity > 0);
      const err = body.querySelector("#rt-error"); err.innerHTML = "";
      body.querySelectorAll(".is-invalid").forEach((x) => x.classList.remove("is-invalid"));
      if (!lines.length) { err.innerHTML = '<div class="alert alert-warning py-2">Enter a quantity for at least one item.</div>'; return null; }
      const bad = lines.find((l) => l.quantity > l.max); if (bad) { bad.el.classList.add("is-invalid"); err.innerHTML = `<div class="alert alert-warning py-2">You can return at most ${App.qty(bad.max)} of that item.</div>`; return null; }
      return lines;
    };
    const exBtn = body.querySelector("#rt-exchange");
    if (exBtn) exBtn.addEventListener("click", async () => {
      const lines = picked(); if (!lines) return;
      if (S.exchange) { App.toast("warning", "An exchange is already open", "Finish or cancel it first."); return; }
      if (S.warehouse !== sale.warehouse && S.cart.length) { body.querySelector("#rt-error").innerHTML = '<div class="alert alert-warning py-2">The current cart is from another warehouse. Clear or finish it first, then start the exchange.</div>'; return; }
      await App.busy(exBtn, async () => {
        try {
          const returns = lines.map(({ sale_item_id, quantity }) => ({ sale_item_id, quantity: String(quantity) }));
          const pv = await App.api.post(`/api/v1/sales/${sale.id}/exchange/preview/`, { returns, surcharge: String(num(body.querySelector("#rt-sur").value)) });
          S.exchange = { sale_id: sale.id, reference: sale.reference, lines: returns, credit: pv.credit, return_value: pv.return_value, restock: body.querySelector("#rt-restock").checked, note: body.querySelector("#rt-note").value.trim(), customer_name: sale.customer_name };
          S.warehouse = sale.warehouse; const sel = document.querySelector('select[aria-label="Warehouse"]'); if (sel) sel.value = sale.warehouse;
          setCustomer({ id: sale.customer, name: sale.customer_name, phone: sale.customer_phone });
          d.close(); S.idem = App.uuid(); changed(); loadProducts(true); $("search").focus();
          App.toast("info", "Exchange started", `Add the replacement items. The ${App.money(pv.credit)} credit is applied at payment.`);
        } catch (er) { body.querySelector("#rt-error").innerHTML = `<div class="alert alert-danger d-flex gap-2 mb-0" role="alert"><i class="bi bi-exclamation-triangle-fill"></i><div>${App.esc(er.message)}</div></div>`; }
      });
    });
    body.querySelector("#rt-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const lines = Array.from(body.querySelectorAll("[data-ri]")).map((i) => ({ sale_item_id: Number(i.dataset.ri), quantity: num(i.value), max: num(i.dataset.max), el: i })).filter((l) => l.quantity > 0);
      const err = body.querySelector("#rt-error"); err.innerHTML = "";
      if (!lines.length) { err.innerHTML = '<div class="alert alert-warning py-2">Enter a quantity for at least one item.</div>'; return; }
      const bad = lines.find((l) => l.quantity > l.max); if (bad) { bad.el.classList.add("is-invalid"); err.innerHTML = `<div class="alert alert-warning py-2">You can return at most ${App.qty(bad.max)} of that item.</div>`; return; }
      if (!(await App.confirm({ title: "Process this return?", message: `${lines.length} item line${lines.length > 1 ? "s" : ""} will be returned${body.querySelector("#rt-restock").checked ? " to stock" : ""} and the refund recorded against ${App.esc(sale.reference)}.`, confirmText: "Process return", tone: "warning", icon: "arrow-return-left" }))) return;
      await App.busy(e.target.querySelector("[type=submit]"), async () => {
        try {
          const r = await App.api.post(`/api/v1/sales/${sale.id}/returns/`, { lines: lines.map(({ sale_item_id, quantity }) => ({ sale_item_id, quantity: String(quantity) })), surcharge: String(num(body.querySelector("#rt-sur").value)), refund_method: body.querySelector("#rt-method").value, note: body.querySelector("#rt-note").value, restock: body.querySelector("#rt-restock").checked });
          body.innerHTML = `<div class="state-box"><div class="state-icon" style="background:var(--app-success-soft);color:var(--app-success)"><i class="bi bi-check2-circle"></i></div><h3>Return recorded</h3><p>${App.esc(r.reference)} · refund <strong>${App.money(r.refunded)}</strong> of ${App.money(r.grand_total)}</p><button class="btn btn-primary" data-bs-dismiss="modal">Done</button></div>`;
          if (S.register && S.register.open) refreshRegister(); loadProducts(true);
        } catch (er) { err.innerHTML = `<div class="alert alert-danger d-flex gap-2 mb-0" role="alert"><i class="bi bi-exclamation-triangle-fill"></i><div>${App.esc(er.message)}</div></div>`; }
      });
    });
  }

  async function cancelExchange() {
    if (!S.exchange) return;
    if (!(await App.confirm({ title: "Cancel this exchange?", message: "Nothing has been returned yet. The cart stays as it is and the sale becomes a normal one.", confirmText: "Cancel exchange", tone: "warning", icon: "x-circle" }))) return;
    S.exchange = null; S.idem = App.uuid(); changed(); App.toast("info", "Exchange cancelled");
  }

  /* ---------------------------------------------------------------- register */
  async function refreshRegister() {
    try { S.register = await App.api.get(API + "register/"); } catch (e) { return; }
    const open = S.register && S.register.open;
    $("register-label").textContent = open ? `Register · ${S.register.sales_count} sales` : "Open register";
    $("btn-register").classList.toggle("btn-outline-secondary", open); $("btn-register").classList.toggle("btn-warning", !open);
  }
  /* ---------------------------------------------------------------- expense from the till */
  function openExpense(afterSave) {
    if (!S.register || !S.register.open) { App.toast("warning", "Open the register first", "Expenses are recorded against your register."); openRegister(false); return; }
    const cats = S.cfg.expense_categories || [], general = cats.find((c) => c.name.toLowerCase() === "general");
    const drawer = num(S.register.expected_cash);
    const d = App.dialog({
      title: '<i class="bi bi-wallet2 me-2"></i>Add expense', size: "dialog-compact", scroll: false,
      body: `<form id="ex-form" novalidate>
        <div class="mb-3"><label class="form-label" for="ex-amt">Amount</label><div class="input-group"><span class="input-group-text">${App.esc(App.config.currency || "")}</span><input class="form-control form-control-lg text-end" id="ex-amt" inputmode="decimal" autocomplete="off" placeholder="0.00" aria-describedby="ex-amt-err"></div><div class="invalid-feedback d-block" id="ex-amt-err" role="alert"></div></div>
        <div class="mb-3"><label class="form-label" for="ex-cat">Category</label><select class="form-select" id="ex-cat">${general ? "" : '<option value="">General</option>'}${cats.map((c) => `<option value="${c.id}" ${general && c.id === general.id ? "selected" : ""}>${App.esc(c.name)}</option>`).join("")}</select></div>
        <div class="mb-3"><label class="form-label" for="ex-note">What was it for? <span class="text-muted">(optional)</span></label><input class="form-control" id="ex-note" maxlength="200" placeholder="e.g. Tea for staff, rickshaw fare" autocomplete="off"></div>
        <div class="form-check form-switch"><input class="form-check-input" type="checkbox" role="switch" id="ex-till" checked><label class="form-check-label" for="ex-till">Paid from till cash</label></div>
        <div class="form-text mb-0" id="ex-hint">Cash in drawer now: <strong>${App.money(drawer)}</strong>. The amount is taken off the expected cash when you close.</div>
        <div id="ex-err" role="alert"></div></form>`,
      footer: '<button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button><button type="submit" form="ex-form" class="btn btn-primary" data-ok><i class="bi bi-check2"></i>Save expense</button>',
      onShown: (el) => el.querySelector("#ex-amt").focus(),
    });
    const amt = d.el.querySelector("#ex-amt"), till = d.el.querySelector("#ex-till");
    till.addEventListener("change", () => { d.el.querySelector("#ex-hint").innerHTML = till.checked ? `Cash in drawer now: <strong>${App.money(drawer)}</strong>. The amount is taken off the expected cash when you close.` : "Recorded against this register, but the drawer cash is not changed."; });
    d.el.querySelector("#ex-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const errBox = d.el.querySelector("#ex-amt-err"); errBox.textContent = ""; amt.classList.remove("is-invalid"); d.el.querySelector("#ex-err").innerHTML = "";
      const v = num(amt.value);
      if (!(v > 0)) { amt.classList.add("is-invalid"); errBox.textContent = "Enter an amount greater than zero."; amt.focus(); return; }
      await App.busy(d.el.querySelector("[data-ok]"), async () => {
        try {
          await App.api.post(API + "expenses/", { amount: String(v), category: d.el.querySelector("#ex-cat").value || null, note: d.el.querySelector("#ex-note").value, paid_from_till: till.checked });
          d.close(); App.toast("success", "Expense saved", `${App.money(v)}${till.checked ? " taken from the till" : ""}`);
          await refreshRegister(); if (afterSave) afterSave();
        } catch (err) {
          const m = err.fields && err.fields.amount;
          if (m) { amt.classList.add("is-invalid"); errBox.textContent = [].concat(m).join(" "); amt.focus(); }
          else d.el.querySelector("#ex-err").innerHTML = `<div class="alert alert-danger mt-3 mb-0">${App.esc(err.message)}</div>`;
        }
      });
    });
  }

  function openRegister(forced) {
    const reg = S.register;
    if (!reg || !reg.open) {
      const d = App.dialog({
        title: "Open cash register", size: "modal-sm", static: !!forced, scroll: false,
        body: `<p class="text-muted">Count the cash in the drawer to start your shift.</p><form id="rg-form" novalidate><label class="form-label" for="rg-cash">Cash in hand</label><div class="input-group"><span class="input-group-text">${App.esc(App.config.currency || "")}</span><input class="form-control form-control-lg" id="rg-cash" inputmode="decimal" value="0"></div><div id="rg-err"></div></form>`,
        footer: `${forced ? '<a class="btn btn-outline-secondary" href="/">Leave POS</a>' : '<button class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>'}<button class="btn btn-primary" data-ok>Open register</button>`,
        onShown: (el) => el.querySelector("#rg-cash").select(),
      });
      const go = async () => { await App.busy(d.el.querySelector("[data-ok]"), async () => { try { await App.api.post(API + "register/open/", { cash_in_hand: String(num(d.el.querySelector("#rg-cash").value)) }); d.close(); await refreshRegister(); App.toast("success", "Register opened"); $("search").focus(); } catch (e) { d.el.querySelector("#rg-err").innerHTML = `<div class="alert alert-danger mt-3 mb-0">${App.esc(e.message)}</div>`; } }); };
      d.el.querySelector("[data-ok]").addEventListener("click", go); d.el.querySelector("#rg-form").addEventListener("submit", (e) => { e.preventDefault(); go(); });
      return;
    }
    const rows = Object.entries(reg.received || {});
    const d = App.dialog({
      title: "Register summary", size: "", scroll: true,
      body: `<div class="row g-3 mb-3"><div class="col-6"><div class="kpi"><div class="kpi-label">Sales</div><div class="kpi-value">${reg.sales_count}</div></div></div><div class="col-6"><div class="kpi"><div class="kpi-label">Sales value</div><div class="kpi-value" style="font-size:1.1rem;white-space:nowrap">${App.money(reg.sales_total)}</div></div></div><div class="col-6"><div class="kpi"><div class="kpi-label">Received</div><div class="kpi-value" style="font-size:1.1rem;white-space:nowrap">${App.money(reg.total_received)}</div></div></div><div class="col-6"><div class="kpi"><div class="kpi-label">Refunds</div><div class="kpi-value" style="font-size:1.1rem;white-space:nowrap">${App.money(reg.total_refunds)}</div></div></div></div>
        <table class="table"><tbody><tr><td>Opening cash</td><td class="num">${App.money(reg.cash_in_hand)}</td></tr>${rows.map(([m, v]) => `<tr><td class="text-capitalize">${App.esc(m.replace(/_/g, " "))} received</td><td class="num">${App.money(v)}</td></tr>`).join("")}${num(reg.expenses_from_till) > 0 ? `<tr><td>Expenses paid from till</td><td class="num text-danger">− ${App.money(reg.expenses_from_till)}</td></tr>` : ""}<tr class="fw-bold"><td>Expected cash in drawer</td><td class="num">${App.money(reg.expected_cash)}</td></tr></tbody></table>
        <div class="d-flex justify-content-between align-items-center mt-3 mb-2"><div class="fw-semibold">Expenses${reg.expenses_count ? ` <span class="badge badge-soft-secondary">${reg.expenses_count}</span>` : ""}</div>${S.cfg.can_expense ? '<button type="button" class="btn btn-sm btn-outline-primary" id="rg-add-exp"><i class="bi bi-plus-lg"></i>Add expense</button>' : ""}</div>
        <div id="rg-exp" aria-live="polite">${reg.expenses_count ? '<div class="skeleton skeleton-line"></div>' : '<div class="text-muted small">No expenses in this shift.</div>'}</div>
        <div class="form-section mt-3"><div class="form-section-title">Close register</div><div class="row g-2"><div class="col-sm-5"><label class="form-label" for="rc-count">Counted cash</label><input class="form-control" id="rc-count" inputmode="decimal" placeholder="${App.num(reg.expected_cash)}"></div><div class="col-sm-7"><label class="form-label" for="rc-note">Note</label><input class="form-control" id="rc-note" maxlength="200"></div></div></div>`,
      footer: `<button class="btn btn-outline-secondary" data-bs-dismiss="modal">Keep open</button><button class="btn btn-danger" data-close><i class="bi bi-lock"></i>Close register</button>`,
    });
    const addBtn = d.el.querySelector("#rg-add-exp");
    if (addBtn) addBtn.addEventListener("click", () => openExpense(() => { d.close(); openRegister(false); }));
    if (reg.expenses_count) App.api.get(API + "expenses/").then((r) => {
      d.el.querySelector("#rg-exp").innerHTML = `<div class="table-wrap"><table class="table table-sm align-middle mb-0"><tbody>${r.results.map((x) => `<tr><td class="text-nowrap text-muted small">${App.esc(new Date(x.date).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</td><td><div class="fw-medium">${App.esc(x.note || x.category_name)}</div>${x.note ? `<div class="cell-sub">${App.esc(x.category_name)}</div>` : ""}</td><td class="text-end"><div class="fw-semibold">${App.money(x.amount)}</div><div class="cell-sub">${x.paid_from_till ? "from till" : "not from till"}</div></td></tr>`).join("")}</tbody></table></div>`;
    }).catch(() => { d.el.querySelector("#rg-exp").innerHTML = '<div class="text-muted small">Could not load the expenses.</div>'; });
    d.el.querySelector("[data-close]").addEventListener("click", async () => {
      const counted = d.el.querySelector("#rc-count").value.trim();
      if (S.cart.length && !(await App.confirm({ title: "Sale in progress", message: "You still have items in the cart. Close the register anyway?", confirmText: "Close anyway", tone: "warning" }))) return;
      await App.busy(d.el.querySelector("[data-close]"), async () => {
        try {
          const r = await App.api.post(API + "register/close/", { counted_cash: counted === "" ? null : String(num(counted)), note: d.el.querySelector("#rc-note").value });
          const diff = r.difference == null ? null : num(r.difference);
          d.el.querySelector(".modal-body").innerHTML = `<div class="state-box"><div class="state-icon" style="background:var(--app-success-soft);color:var(--app-success)"><i class="bi bi-lock"></i></div><h3>Register closed</h3><p>Expected ${App.money(r.expected_cash)}${diff == null ? "" : ` · counted ${App.money(r.counted_cash)} · ${diff === 0 ? "balanced" : (diff > 0 ? "over by " : "short by ") + App.money(Math.abs(diff))}`}</p><a class="btn btn-primary" href="/">Back to dashboard</a></div>`;
          d.el.querySelector(".modal-footer").remove(); await refreshRegister();
        } catch (e) { App.toastError(e, "Could not close register"); }
      });
    });
  }

  /* ---------------------------------------------------------------- shortcuts & help */
  const SHORTCUT_LABELS = { focus_search: "Focus search / scan", add_discount: "Discount, tax & shipping", customer: "Choose customer", hold: "Hold sale", resume: "Held sales", pay_cash: "Quick cash payment", pay: "Take payment", clear: "Clear cart", returns: "Return / refund", help: "Show this help" };
  function openHelp() {
    const m = S.cfg.shortcuts;
    const rows = Object.entries(SHORTCUT_LABELS).map(([k, v]) => [v, `<kbd>${App.esc(m[k] || "")}</kbd>`]).concat([["Add first suggestion / scan", "<kbd>Enter</kbd>"], ["Move in suggestions", "<kbd>↑</kbd> <kbd>↓</kbd>"], ["Close window / clear search", "<kbd>Esc</kbd>"]]);
    App.dialog({ title: '<i class="bi bi-keyboard me-2"></i>Keyboard shortcuts', size: "modal-lg", body: `<div class="sc-grid">${rows.map(([l, k]) => `<div class="sc-row"><span>${l}</span><span class="text-nowrap">${k}</span></div>`).join("")}</div>` });
  }
  const ACTIONS = {
    focus_search: () => { $("search").focus(); $("search").select(); }, add_discount: openAdjust, customer: openCustomer, hold: holdSale, resume: openHeld,
    pay_cash: () => openPayment("cash"), pay: () => openPayment(), clear: () => clearCart(true), returns: () => openReturns(), help: openHelp,
  };
  document.addEventListener("keydown", (e) => {
    if (document.querySelector(".modal.show")) return;
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
    if (e.key === "Escape") { if (S.q) resetSearch(); return; }
    for (const [name, key] of Object.entries(S.cfg.shortcuts || {})) {
      if (e.key === key && (!typing || key.startsWith("F")) && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); ACTIONS[name] && ACTIONS[name](); return; }
    }
    // scanners "type" into whatever has focus: route stray printable keys to the search box
    if (!typing && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== " ") $("search").focus();
  });

  /* ---------------------------------------------------------------- wiring */
  let scanAt = -1000;
  function wire() {
    const search = $("search");
    // registered first so it sees Enter/Tab before the handler below: strips the scanner prefix, beeps, and submits fast scans
    App.scanner.watch(search, (code, info) => { if (info.fast) { scanAt = performance.now(); search.value = code; S.sugIndex = -1; submitSearch(true); } });
    $("btn-camera").addEventListener("click", () => App.scanner.camera({ title: "Scan product barcode", onScan: (code) => { search.value = code; S.sugIndex = -1; submitSearch(true); } }));
    search.addEventListener("input", (e) => onSearch(e.target.value.trim()));
    search.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { if (!S.sugItems.length) return; e.preventDefault(); S.sugIndex = (S.sugIndex + (e.key === "ArrowDown" ? 1 : -1) + S.sugItems.length) % S.sugItems.length; renderSuggest(); }
      else if (e.key === "Enter") { e.preventDefault(); if (performance.now() - scanAt > 60) submitSearch(); }
    });
    search.addEventListener("blur", () => setTimeout(() => $("suggest").classList.add("d-none"), 150));
    search.addEventListener("focus", () => renderSuggest());
    $("suggest").addEventListener("mousedown", (e) => { const it = e.target.closest("[data-s]"); if (it) { e.preventDefault(); addProduct(S.sugItems[Number(it.dataset.s)]); resetSearch(); } });
    $("grid").addEventListener("click", (e) => { const t = e.target.closest("[data-i]"); if (t) addProduct(S.products[Number(t.dataset.i)]); });
    $("btn-more").addEventListener("click", () => loadProducts(false));
    new IntersectionObserver((en) => { if (en[0].isIntersecting && !S.loading && S.products.length < S.total) loadProducts(false); }, { root: $("grid-wrap"), rootMargin: "300px" }).observe($("grid-more"));
    $("cats").addEventListener("click", (e) => { const c = e.target.closest("[data-cat]"); if (!c) return; S.category = c.dataset.cat ? Number(c.dataset.cat) : null; renderCats(); loadProducts(true); });
    $("btn-customer").addEventListener("click", openCustomer); $("btn-discount").addEventListener("click", openAdjust);
    $("btn-pay").addEventListener("click", () => openPayment()); $("btn-hold").addEventListener("click", holdSale); $("btn-clear").addEventListener("click", () => clearCart(true));
    $("btn-held").addEventListener("click", openHeld); $("btn-returns").addEventListener("click", () => openReturns()); $("btn-register").addEventListener("click", () => openRegister(false)); if (S.cfg.can_expense) { $("btn-expense").classList.remove("d-none"); $("btn-expense").addEventListener("click", () => openExpense()); } $("btn-help").addEventListener("click", openHelp);
    $("cart-fab").addEventListener("click", () => $("cart-panel").classList.add("open")); $("btn-close-cart").addEventListener("click", () => $("cart-panel").classList.remove("open"));
    document.querySelector("[data-theme-toggle]").addEventListener("click", App.toggleTheme);
    window.addEventListener("beforeunload", (e) => { if (S.cart.length) { e.preventDefault(); e.returnValue = ""; } });
  }

  /* ---------------------------------------------------------------- boot */
  async function boot() {
    $("grid").innerHTML = Array.from({ length: 12 }, () => '<div class="p-tile" style="pointer-events:none"><div class="skeleton" style="height:78px;border-radius:0"></div><div class="p-body"><div class="skeleton skeleton-line"></div></div></div>').join("");
    try {
      const [cfg, cats] = await Promise.all([App.api.get(API + "config/"), App.api.get(API + "categories/")]);
      S.cfg = cfg; S.categories = cats; S.warehouse = cfg.default_warehouse;
      App.receipt.setPrinters(cfg.printers); App.scanner.configure(cfg.scanners);
      if (cfg.scanners.some((x) => x.kind === "camera") && App.scanner.supportsCamera()) $("btn-camera").classList.remove("d-none");
      App.config.currency = cfg.currency; App.config.currency_position = cfg.currency_position; App.config.decimals = cfg.decimals; App.config.qty_decimals = cfg.qty_decimals;
      wire(); renderCats();
      if (cfg.default_customer) setCustomer(cfg.default_customer); else { $("cust-name").textContent = "No customer selected"; $("cust-meta").textContent = "Required to start a sale"; }
      if (cfg.warehouses.length > 1) {
        const sel = document.createElement("select"); sel.className = "form-select"; sel.style.maxWidth = "170px"; sel.setAttribute("aria-label", "Warehouse");
        sel.innerHTML = cfg.warehouses.map((w) => `<option value="${w.id}" ${w.id === S.warehouse ? "selected" : ""}>${App.esc(w.name)}</option>`).join("");
        sel.addEventListener("change", async () => { if (S.exchange) { sel.value = S.warehouse; App.toast("info", "Warehouse is fixed for an exchange", "Finish or cancel the exchange first."); return; } if (S.cart.length && !(await App.confirm({ title: "Change warehouse?", message: "The current cart will be cleared because stock differs per warehouse.", confirmText: "Change", tone: "warning" }))) { sel.value = S.warehouse; return; } S.warehouse = Number(sel.value); await clearCart(false); loadProducts(true); });
        $("btn-register").insertAdjacentElement("beforebegin", sel);
      }
      render(); loadProducts(true); refreshHeld();
      await refreshRegister();
      if (cfg.require_register && !(S.register && S.register.open)) openRegister(true);
      const wantEx = new URLSearchParams(location.search).get("exchange");
      if (wantEx && /^\d+$/.test(wantEx)) { history.replaceState(null, "", location.pathname); if (cfg.can_return) { if (!cfg.require_register || (S.register && S.register.open)) openReturns(wantEx); } else App.toast("warning", "Not allowed", "You do not have permission to return or exchange sales."); }
      $("search").focus();
    } catch (e) {
      $("grid").innerHTML = ""; $("grid-state").innerHTML = App.errorState(e, "Reload");
      $("grid-state").querySelector("[data-retry]")?.addEventListener("click", () => location.reload());
    }
  }
  boot();
})();
