/* Add / edit product page. Plain JavaScript (no Alpine), so the form works even if the CDN scripts are slow or blocked.

   How it hangs together
   - The server renders one formset row per variant. A product WITHOUT variants uses only the first row: its Cost, Selling price,
     Barcode and Opening-qty inputs are moved up into the "Basic information / Pricing / Opening stock" cards (same inputs, same
     names, so saving needs no special case). Switching "This product has variants" on moves them back into the table.
   - Everything the page does is progressive: without this file the table of variants is still a working form. */
(function () {
  "use strict";

  function ready(fn) { if (document.readyState !== "loading") fn(); else document.addEventListener("DOMContentLoaded", fn); }

  ready(function () {
    var form = document.getElementById("pf");
    if (!form) return;

    var $ = function (sel, root) { return (root || form).querySelector(sel); };
    var $$ = function (sel, root) { return Array.prototype.slice.call((root || form).querySelectorAll(sel)); };
    var isNew = form.dataset.new === "1";
    var MAX_BYTES = 3 * 1024 * 1024;
    var SLOTS = ["barcode", "cost_price", "selling_price", "opening_stock"];

    var typeSelect = $('select[name="product_type"]');
    var variantsBox = $("[data-variants]");
    var totalForms = document.getElementById("id_variants-TOTAL_FORMS");
    var template = document.getElementById("variant-template");
    var hasVariants = document.getElementById("pf-has-variants");
    var mode = form.dataset.mode === "variant" ? "variant" : "single";
    var startedAsVariants = mode === "variant";

    function esc(t) { return String(t == null ? "" : t).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
    function toast(kind, msg) { try { if (window.toast && window.toast[kind]) window.toast[kind](msg); } catch (e) { /* inline messages are the fallback */ } }

    /* ------------------------------------------------------------------ variant rows */
    function rows() { return $$("[data-variants] > [data-variant]"); }
    function isGone(row) { var box = $('input[type=checkbox][name$="-DELETE"]', row); return !!(box && box.checked); }
    function liveRows() { return rows().filter(function (r) { return !isGone(r); }); }
    // a row's input; for the first row of a plain product the input has been moved up into a card, so look there too
    function field(row, name) {
      var el = $('[name$="-' + name + '"]', row);
      if (!el && row && row === rows()[0]) el = $('[data-slot] [name$="-' + name + '"]');
      return el;
    }

    // single product: pull the first row's inputs into the cards; variants: put them back in the table
    function moveToCards() {
      var row = rows()[0];
      if (!row) return;
      SLOTS.forEach(function (key) {
        var cell = $('[data-cell="' + key + '"]', row), slot = $('[data-slot="' + key + '"]');
        if (!cell || !slot) return;
        while (cell.firstChild) slot.appendChild(cell.firstChild);
        var input = $("input", slot), label = slot.parentNode.querySelector("label.ui-label");
        if (input && label && input.id) label.setAttribute("for", input.id);
        if (input) input.placeholder = key === "selling_price" ? "0.00" : key === "cost_price" ? "0.00" : "0";
      });
    }
    function moveToTable() {
      var row = rows()[0];
      if (!row) return;
      SLOTS.forEach(function (key) {
        var cell = $('[data-cell="' + key + '"]', row), slot = $('[data-slot="' + key + '"]');
        if (!cell || !slot) return;
        while (slot.firstChild) cell.appendChild(slot.firstChild);
        var input = $("input", cell);
        if (input && (key === "cost_price" || key === "selling_price")) input.placeholder = "Same as first";
      });
    }

    function countDropped() {
      var n = $$("[data-variants] [data-auto-drop]").length, box = document.getElementById("pf-dropped");
      if (box) { box.hidden = !(mode === "single" && n > 0); document.getElementById("pf-dropped-n").textContent = n; }
    }

    // Variants off: the first row stays as the product's one variant. New extra rows vanish; saved ones are ticked for removal and
    // hidden, so you see what Save will do. Switch variants back on to undo.
    function setMode(next, initial) {
      mode = next;
      form.dataset.mode = next;
      typeSelect.value = next === "variant" ? "variant" : "single";
      if (hasVariants) hasVariants.checked = next === "variant";
      var list = rows();
      if (next === "single") {
        for (var i = list.length - 1; i >= 1 && !initial; i--) {
          var r = list[i], box = $('input[type=checkbox][name$="-DELETE"]', r);
          if (!box) { r.remove(); continue; }
          if (!box.checked) { box.checked = true; r.dataset.autoDrop = "1"; }
          r.classList.add("is-removed");
        }
        var keep = list[0], keepBox = keep && $('input[type=checkbox][name$="-DELETE"]', keep);
        if (keepBox && !initial) keepBox.checked = false;
        moveToCards();
      } else {
        moveToTable();
        if (!initial) list.forEach(function (r) {
          if (r.dataset.autoDrop) { var b = $('input[type=checkbox][name$="-DELETE"]', r); if (b) b.checked = false; delete r.dataset.autoDrop; }
          r.classList.remove("is-removed");
        });
      }
      countDropped();
      showPreview(currentCode());
    }

    function addRow(name) {
      if (!totalForms || !template) return null;
      var index = parseInt(totalForms.value, 10) || 0;
      var wrap = document.createElement("div");
      wrap.innerHTML = template.innerHTML.replace(/__prefix__/g, index);
      var row = wrap.firstElementChild;
      variantsBox.appendChild(row);
      totalForms.value = index + 1;
      var nm = field(row, "name");
      if (nm) { nm.value = name || ""; if (name) nm.dataset.auto = name; }
      ["cost_price", "selling_price"].forEach(function (f) { var el = field(row, f); if (el) el.placeholder = "Same as first"; });
      if (!name && nm) nm.focus();
      $$('input[name$="-opening_stock"]', row).forEach(function (i) { i.disabled = form.dataset.itype && form.dataset.itype !== "standard"; });
      return row;
    }

    function quickAdd() {
      var input = document.getElementById("pv-quick-input");
      var names = input.value.split(",").map(function (n) { return n.trim(); }).filter(Boolean);
      if (!names.length) { input.focus(); return; }
      var have = {}, skipped = 0;
      $$("[data-variants] input[name$='-name']").forEach(function (i) { if (!isGone(i.closest("[data-variant]"))) have[i.value.trim().toLowerCase()] = true; });
      names.forEach(function (n) {
        if (have[n.toLowerCase()]) { skipped++; return; }
        have[n.toLowerCase()] = true;
        var list = liveRows(), only = list.length === 1 ? list[0] : null, nameInput = only && field(only, "name");
        // the lone "Default" row (its stock stays with it) becomes the first variant instead of sitting beside the new ones
        if (only && nameInput && (nameInput.value.trim() === "" || nameInput.value.trim().toLowerCase() === "default")) { nameInput.value = n; delete have["default"]; }
        else addRow(n);
      });
      input.value = "";
      if (skipped) toast("info", "Some names were skipped: variant names must be different from each other.");
    }

    // one value per attribute: ticking Large un-ticks Small, then the variant name follows the choices
    function pickAttr(box) {
      var card = box.closest("[data-variant]");
      var label = function (o) { return o.parentElement.querySelector("span").textContent.trim(); };
      // what the name was built from BEFORE this click (a name typed by hand is never overwritten)
      var before = [];
      $$(".pf-attr", card).forEach(function (g) {
        var on = $$("input:checked", g).filter(function (o) { return o !== box; })[0];
        if (!box.checked && g.contains(box)) on = box;
        if (on) before.push(label(on));
      });
      if (box.checked) $$("input[type=checkbox]", box.closest(".pf-attr-opts")).forEach(function (o) { if (o !== box) o.checked = false; });
      var name = field(card, "name");
      if (!name) return;
      var picked = [];
      $$(".pf-attr", card).forEach(function (g) { var on = $("input:checked", g); if (on) picked.push(label(on)); });
      var cur = name.value.trim();
      if (!cur || cur === "Default" || cur === name.dataset.auto || cur === before.join(" / ")) { name.value = picked.join(" / "); name.dataset.auto = name.value; }
    }

    if (variantsBox) {
      variantsBox.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-act]");
        if (!btn) return;
        var row = btn.closest("[data-variant]");
        if (btn.dataset.act === "more") {
          var panel = $("[data-more]", row), open = panel.hidden;
          panel.hidden = !open;
          btn.setAttribute("aria-expanded", open ? "true" : "false");
        } else if (btn.dataset.act === "remove-row" && rows().length > 1) {
          row.remove();
        }
      });
      variantsBox.addEventListener("change", function (e) { if (e.target.matches && e.target.matches("input[data-attr]")) pickAttr(e.target); });
    }
    var quickBtn = document.getElementById("pv-quick-btn"), quickInput = document.getElementById("pv-quick-input");
    if (quickBtn) quickBtn.addEventListener("click", quickAdd);
    if (quickInput) quickInput.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); quickAdd(); } });
    var addRowBtn = document.getElementById("pv-add-row");
    if (addRowBtn) addRowBtn.addEventListener("click", function () { addRow(""); });
    if (hasVariants) hasVariants.addEventListener("change", function () { setMode(hasVariants.checked ? "variant" : "single"); if (hasVariants.checked && quickInput) quickInput.focus(); });

    /* ------------------------------------------------------------------ category / sub-category (one real field: category) */
    (function categories() {
      var raw = document.getElementById("pf-cats"), real = $('select[name="category"]'), top = document.getElementById("pf-cat"), sub = document.getElementById("pf-sub");
      if (!raw || !real || !top || !sub) return;
      var cats = JSON.parse(raw.textContent || "[]");
      function byId(id) { for (var i = 0; i < cats.length; i++) if (cats[i].id === id) return cats[i]; return null; }
      function fill(select, list, blank, value) {
        select.innerHTML = "";
        select.appendChild(new Option(blank, ""));
        list.forEach(function (c) { select.appendChild(new Option(c.name, c.id)); });
        select.value = value ? String(value) : "";
      }
      function fillSub(selected) {
        var subs = cats.filter(function (c) { return c.parent_id === Number(top.value); });
        fill(sub, subs, subs.length ? "— None —" : "No sub-categories", selected);
        sub.disabled = !subs.length;
      }
      var cur = real.value ? byId(Number(real.value)) : null, chain = [];
      while (cur) { chain.unshift(cur); cur = cur.parent_id ? byId(cur.parent_id) : null; }
      fill(top, cats.filter(function (c) { return !c.parent_id; }), "— No category —", chain[0] && chain[0].id);
      fillSub(chain[1] && chain[1].id);
      top.addEventListener("change", function () { fillSub(""); real.value = top.value; });
      sub.addEventListener("change", function () { real.value = sub.value || top.value; });
    })();

    /* ------------------------------------------------------------------ barcode: preview + generate */
    var bcBox = document.getElementById("pf-bc-box"), bcTimer = null, lastFocusedCode = null;
    var BC_EMPTY = "Enter a code to see the barcode.";

    function codeInput() {
      if (mode === "single") { var r = rows()[0]; return r ? field(r, "barcode") || $('[data-slot="barcode"] input') : null; }
      if (lastFocusedCode && document.body.contains(lastFocusedCode)) return lastFocusedCode;
      var first = null;
      liveRows().some(function (r) { var el = field(r, "barcode"); if (el && el.value.trim()) { first = el; return true; } return false; });
      return first;
    }
    function currentCode() { var el = codeInput(); return el ? el.value.trim() : ""; }
    var symSelect = $('select[name="barcode_symbology"]');
    function symbology() { return symSelect && symSelect.value ? symSelect.value : "code128"; }
    function showPreview(value) {                      // drawn in the browser, in the barcode type chosen above (the label printer uses the same)
      clearTimeout(bcTimer);
      if (!bcBox) return;
      bcTimer = setTimeout(function () {
        if (!value) { bcBox.innerHTML = '<span>' + BC_EMPTY + '</span>'; return; }
        var r = window.App && window.App.barcode ? window.App.barcode.svg(value, symbology(), { height: 56 }) : null;
        bcBox.innerHTML = r && r.svg ? r.svg : '<span>' + esc(r && r.error ? r.error : "This text cannot be drawn as a barcode.") + '</span>';
      }, 120);
    }
    if (symSelect) symSelect.addEventListener("change", function () { showPreview(currentCode()); });
    form.addEventListener("input", function (e) { if (e.target.name && /-barcode$/.test(e.target.name)) { lastFocusedCode = e.target; if (e.target === codeInput()) showPreview(e.target.value.trim()); } });
    form.addEventListener("focusin", function (e) { if (e.target.name && /-barcode$/.test(e.target.name)) { lastFocusedCode = e.target; showPreview(e.target.value.trim()); } });

    var genBtn = document.getElementById("pf-generate"), genArmed = null;
    function setGenLabel(text) { var s = genBtn && genBtn.querySelector("span"); if (s) s.textContent = text; }
    if (genBtn) genBtn.addEventListener("click", function () {
      var input = codeInput();
      if (!input) return;
      if (!isNew && input.value.trim() && !genArmed) {                     // replacing a saved barcode breaks printed labels: ask once, inline
        setGenLabel("Click again to replace");
        genArmed = setTimeout(function () { genArmed = null; setGenLabel("Generate"); }, 3500);
        return;
      }
      clearTimeout(genArmed); genArmed = null; setGenLabel("Generate");
      genBtn.disabled = true;
      fetch(form.dataset.generateUrl + "?type=" + encodeURIComponent(symbology()), { credentials: "same-origin", headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || "Could not make a code."); return d; }); })
        .then(function (d) { input.value = d.code; input.classList.remove("is-invalid"); input.dispatchEvent(new Event("input", { bubbles: true })); })
        .catch(function (err) { showError(input, err.message || "Could not make a code. Check your connection and try again."); })
        .then(function () { genBtn.disabled = false; });
    });

    /* ------------------------------------------------------------------ pictures */
    var cover = $('input[name="image"]'), coverImg = document.getElementById("pf-cover-img"), coverEmpty = document.getElementById("pf-cover-empty");
    var coverMsg = document.getElementById("pf-cover-msg"), removeCover = $('input[name="remove_image"]');
    var savedCover = form.dataset.cover || "";
    function showCover(src) {
      if (src) { coverImg.src = src; coverImg.hidden = false; coverEmpty.hidden = true; }
      else { coverImg.hidden = true; coverImg.removeAttribute("src"); coverEmpty.hidden = false; }
    }
    if (cover) cover.addEventListener("change", function () {
      var f = cover.files && cover.files[0];
      coverMsg.hidden = true;
      if (!f) { showCover(removeCover && removeCover.checked ? "" : savedCover); return; }
      if (f.size > MAX_BYTES) { cover.value = ""; coverMsg.textContent = "The cover picture is larger than 3 MB."; coverMsg.hidden = false; showCover(savedCover); return; }
      if (!/^image\//.test(f.type)) { cover.value = ""; coverMsg.textContent = "“" + f.name + "” is not a picture."; coverMsg.hidden = false; showCover(savedCover); return; }
      if (removeCover) removeCover.checked = false;
      showCover(URL.createObjectURL(f));
    });
    if (removeCover) removeCover.addEventListener("change", function () { if (removeCover.checked) { if (cover) cover.value = ""; showCover(""); } else showCover(savedCover); });

    var galInput = document.getElementById("pf-gal-input"), galGrid = document.getElementById("pf-gallery"), galAdd = document.getElementById("pf-gal-add");
    var galMsg = document.getElementById("pf-gal-msg"), galNone = document.getElementById("pf-gal-none"), galCount = document.getElementById("pf-gal-count");
    var galMax = parseInt(form.dataset.galleryMax, 10) || 8, fresh = [], seq = 0;
    function galleryTotal() { return $$("figure[data-existing]:not(.is-gone)", galGrid).length + fresh.length; }
    function galleryRefresh() {
      var total = galleryTotal(), shown = $$("figure", galGrid).length;
      galCount.textContent = total ? "· " + total + " of " + galMax : "";
      galAdd.disabled = total >= galMax;
      galGrid.hidden = !shown; galNone.hidden = !!shown;
    }
    function syncGalleryInput() {
      try { var dt = new DataTransfer(); fresh.forEach(function (p) { dt.items.add(p.file); }); galInput.files = dt.files; }
      catch (e) { galMsg.textContent = "This browser cannot keep several pictures at once. Pick them in one go."; galMsg.hidden = false; }
    }
    function addFresh(p) {
      var fig = document.createElement("figure");
      fig.className = "pf-pic is-new"; fig.dataset.key = p.key;
      var img = document.createElement("img"); img.src = p.url; img.alt = "New gallery picture";
      var btn = document.createElement("button"); btn.type = "button"; btn.dataset.act = "drop-fresh"; btn.setAttribute("aria-label", "Remove this new picture");
      btn.innerHTML = '<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>';
      fig.appendChild(img); fig.appendChild(btn); galGrid.appendChild(fig);
    }
    if (galAdd) galAdd.addEventListener("click", function () { galInput.click(); });
    if (galInput) galInput.addEventListener("change", function () {
      var picked = Array.prototype.slice.call(galInput.files || []), notes = [];
      picked.forEach(function (f) {
        if (fresh.some(function (p) { return p.file === f; })) return;
        if (!/^image\//.test(f.type)) { notes.push("“" + f.name + "” is not a picture."); return; }
        if (f.size > MAX_BYTES) { notes.push("“" + f.name + "” is larger than 3 MB."); return; }
        if (galleryTotal() >= galMax) { notes.push("You can keep at most " + galMax + " gallery pictures."); return; }
        var p = { key: ++seq, file: f, url: URL.createObjectURL(f) };
        fresh.push(p); addFresh(p);
      });
      galMsg.textContent = notes.filter(function (n, i) { return notes.indexOf(n) === i; }).join(" "); galMsg.hidden = !notes.length;
      syncGalleryInput(); galleryRefresh();
    });
    if (galGrid) galGrid.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-act]");
      if (!btn) return;
      var fig = btn.closest("figure");
      galMsg.hidden = true;
      if (btn.dataset.act === "toggle-pic") {
        var gone = fig.classList.toggle("is-gone");
        $("input[type=checkbox]", fig).checked = gone;
        btn.setAttribute("aria-label", gone ? "Keep this picture" : "Remove this picture");
      } else if (btn.dataset.act === "drop-fresh") {
        fresh = fresh.filter(function (p) { if (String(p.key) === fig.dataset.key) { URL.revokeObjectURL(p.url); return false; } return true; });
        fig.remove(); syncGalleryInput();
      }
      galleryRefresh();
    });
    if (galGrid) galleryRefresh();

    /* ------------------------------------------------------------------ checks before sending */
    var errorsBox = document.getElementById("pf-errors"), errorsTitle = document.getElementById("pf-errors-title"), errorsText = document.getElementById("pf-errors-text");
    function showError(input, message) {
      if (!input) return;
      input.classList.add("is-invalid");
      var holder = input.closest(".ui-field") || input.parentNode, p = document.createElement("p");
      p.className = "ui-error"; p.dataset.js = "1"; p.setAttribute("role", "alert"); p.textContent = message;
      holder.appendChild(p);
    }
    function clearErrors() {
      $$("[data-js]").forEach(function (p) { p.remove(); });
      $$(".is-invalid").forEach(function (i) { i.classList.remove("is-invalid"); });
      errorsBox.hidden = true;
    }
    form.addEventListener("input", function (e) {
      if (!e.target.classList || !e.target.classList.contains("is-invalid")) return;
      e.target.classList.remove("is-invalid");
      var holder = e.target.closest(".ui-field") || e.target.parentNode, js = holder && holder.querySelector("[data-js]");
      if (js) js.remove();
      if (!$(".is-invalid")) errorsBox.hidden = true;
    });

    function validate() {
      var bad = [];
      function need(input, message) { if (input) { showError(input, message); bad.push(input); } }
      var name = $('input[name="name"]'), unit = $('select[name="unit"]');
      if (!name.value.trim()) need(name, "Enter the product name.");
      if (unit && !unit.value) need(unit, "Choose a unit. Create one under Products › Units if the list is empty.");
      var live = liveRows();
      if (mode === "single") {
        var row = rows()[0], price = row && field(row, "selling_price");
        if (price && price.value.trim() === "") need(price, "Enter a selling price.");
      } else {
        if (!live.length) { need(quickInput, "Add at least one variant, or switch “This product has variants” off."); }
        var seen = {}, anyPrice = false;
        live.forEach(function (r) {
          var nm = field(r, "name"), key = nm.value.trim().toLowerCase();
          if (!key) need(nm, "Enter a variant name.");
          else if (seen[key]) need(nm, "Each variant needs a different name.");
          seen[key] = true;
          var sp = field(r, "selling_price");
          if (sp && sp.value.trim() !== "") anyPrice = true;
        });
        if (live.length && !anyPrice) need(field(live[0], "selling_price"), "Enter a selling price for at least the first variant.");
      }
      var sym = symbology();
      live.forEach(function (r) {                                           // every barcode has to fit the chosen barcode type
        var bc = field(r, "barcode"), v = bc && bc.value.trim();
        if (!v || !window.App || !window.App.barcode) return;
        var res = window.App.barcode.svg(v, sym, { height: 20 });
        if (res && res.error) need(bc, res.error.replace(/\.?$/, ".") + " Pick another barcode type or press Generate.");
      });
      var promo = $('input[name="promo_price"]'), pstart = $('input[name="promo_start"]'), pend = $('input[name="promo_end"]');
      if (promo && mode === "single") {
        var pv = promo.value.trim(), sp0 = rows()[0] && field(rows()[0], "selling_price");
        if (!pv && ((pstart && pstart.value) || (pend && pend.value))) need(promo, "Enter the promotion price, or clear the dates.");
        if (pv && sp0 && sp0.value.trim() !== "" && parseFloat(pv) >= parseFloat(sp0.value)) need(promo, "The promotion price must be lower than the selling price.");
        if (pstart && pend && pstart.value && pend.value && pend.value < pstart.value) need(pend, "The promotion cannot end before it starts.");
      }
      if (itemType() === "combo") {
        if (!combo.length) need(comboSearch, "Add at least one product to the combo.");
        $$("#pf-combo-body input").forEach(function (i) { if (!(parseFloat(i.value) > 0)) need(i, "Enter a quantity above zero."); });
      }
      return bad;
    }

    var submitting = false;
    form.addEventListener("submit", function (e) {
      if (submitting) { e.preventDefault(); return; }
      clearErrors();
      if (mode === "single") {                                              // the one variant of a plain product is called Default unless named
        var r0 = rows()[0], nm = r0 && field(r0, "name");
        if (nm && (!nm.value.trim() || startedAsVariants)) nm.value = "Default";   // a product turned plain keeps one variant, called Default
      }
      var bad = validate();
      if (bad.length) {
        e.preventDefault();
        errorsTitle.textContent = "Please check the form.";
        errorsText.textContent = bad.length === 1 ? "One field needs your attention." : bad.length + " fields need your attention.";
        errorsBox.hidden = false;
        var first = bad[0];
        if (first.closest("[data-more]")) first.closest("[data-more]").hidden = false;
        first.scrollIntoView({ block: "center", behavior: "smooth" });
        first.focus({ preventScroll: true });
        return;
      }
      submitting = true;
      var sender = e.submitter;
      setTimeout(function () {                                              // after the browser has read the form: stop double clicks
        $$("[data-submit]").forEach(function (b) { b.disabled = true; b.classList.add("is-loading"); });
        if (sender) { var s = sender.querySelector("span"); if (s) s.textContent = "Saving…"; }
      }, 0);
    });
    window.addEventListener("pageshow", function () {                       // back button after a save
      submitting = false;
      $$("[data-submit]").forEach(function (b) { b.disabled = false; b.classList.remove("is-loading"); });
    });
    document.addEventListener("keydown", function (e) {                    // Ctrl/Cmd + S saves
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); var main = $("[data-submit]"); if (main && !submitting) form.requestSubmit(main); }
    });

    /* ------------------------------------------------------------------ product type + combo contents */
    var typeRadios = $$('input[name="item_type"]'), comboBox = document.getElementById("pf-combo"), typeHelp = document.getElementById("pf-type-help");
    var comboInput = $('input[name="combo_items"]'), comboBody = document.getElementById("pf-combo-body"), comboNone = document.getElementById("pf-combo-none");
    var comboSearch = document.getElementById("pf-combo-q"), comboList = document.getElementById("pf-combo-results");
    var combo = [], comboTimer = null, comboSeq = 0;
    var TYPE_HELP = {
      standard: "",
      service: "A service has no stock: it can always be sold, and the stock sections are hidden.",
      combo: "A combo is sold as one item at its own price. When it is sold, the products listed below are taken from stock.",
      digital: "A digital item has no stock: it can always be sold (e.g. a voucher, licence or download)."
    };
    function itemType() { var on = typeRadios.filter(function (r) { return r.checked; })[0]; return on ? on.value : "standard"; }
    function applyType() {
      var t = itemType(), stocked = t === "standard";
      form.dataset.itype = t;
      $$("[data-stock-only]").forEach(function (el) { el.hidden = !stocked; });
      if (comboBox) comboBox.hidden = t !== "combo";
      $$('input[name$="-opening_stock"]').forEach(function (i) { i.disabled = !stocked; });
      if (t === "combo" && mode === "variant") setMode("single");           // one bundle, one price: no variants
      if (hasVariants) { hasVariants.disabled = t === "combo"; var lab = hasVariants.closest("label"); if (lab) lab.title = t === "combo" ? "A combo cannot have variants" : ""; }
      if (typeHelp) typeHelp.textContent = TYPE_HELP[t] || "";
    }
    typeRadios.forEach(function (r) { r.addEventListener("change", applyType); });

    function comboSync() { if (comboInput) comboInput.value = JSON.stringify(combo.map(function (c) { return { variant: c.variant, qty: c.qty }; })); }
    function comboRender() {
      if (!comboBody) return;
      comboBody.innerHTML = combo.map(function (c, i) {
        return '<tr data-i="' + i + '"><td><div class="ui-cell-title">' + esc(c.name) + '</div><div class="ui-cell-sub">' + esc(c.sku) + '</div></td>' +
          '<td class="num"><input type="number" step="any" min="0.001" value="' + esc(c.qty) + '" aria-label="Quantity of ' + esc(c.name) + '" data-combo-qty> <small>' + esc(c.unit || "") + '</small></td>' +
          '<td><button type="button" class="pv-icon pv-del" data-combo-remove aria-label="Remove ' + esc(c.name) + '"><svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg></button></td></tr>';
      }).join("");
      if (comboNone) comboNone.hidden = combo.length > 0;
      comboSync();
    }
    function comboAdd(item) {
      var have = combo.filter(function (c) { return c.variant === item.id; })[0];
      if (have) { have.qty = String((parseFloat(have.qty) || 0) + 1); }
      else combo.push({ variant: item.id, name: item.name, sku: item.sku, unit: item.unit, qty: "1" });
      comboRender();
    }
    function comboClose() { if (comboList) { comboList.hidden = true; comboList.innerHTML = ""; } if (comboSearch) comboSearch.setAttribute("aria-expanded", "false"); }
    function comboFind() {
      var q = comboSearch.value.trim();
      if (q.length < 1) { comboClose(); return; }
      var mine = ++comboSeq;
      fetch(comboSearch.dataset.lookupUrl + "?q=" + encodeURIComponent(q), { credentials: "same-origin", headers: { "Accept": "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error(); return r.json(); })
        .then(function (d) {
          if (mine !== comboSeq) return;
          var list = (d.results || []).filter(function (x) { return x.tracked !== false; }).slice(0, 12);
          comboList.innerHTML = list.length ? list.map(function (x, i) {
            return '<li role="option"><button type="button" data-i="' + i + '"><span class="ui-cell-title">' + esc(x.name) + '</span><span class="ui-cell-sub">' + esc(x.sku) + ' · ' + esc(x.stock) + ' ' + esc(x.unit) + ' in stock</span></button></li>';
          }).join("") : '<li class="pf-combo-empty">No ordinary product matches “' + esc(q) + '”.</li>';
          comboList.hidden = false; comboSearch.setAttribute("aria-expanded", "true");
          comboList._items = list;
          if (list.length === 1 && comboSearch.dataset.enter === "1") { comboAdd(list[0]); comboSearch.value = ""; comboClose(); }
          comboSearch.dataset.enter = "";
        })
        .catch(function () { comboList.innerHTML = '<li class="pf-combo-empty">Could not search. Check your connection and try again.</li>'; comboList.hidden = false; });
    }
    if (comboSearch) {
      comboSearch.addEventListener("input", function () { clearTimeout(comboTimer); comboTimer = setTimeout(comboFind, 220); });
      comboSearch.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); clearTimeout(comboTimer); comboSearch.dataset.enter = "1"; comboFind(); }      // a scanned barcode adds the one match
        else if (e.key === "Escape") comboClose();
        else if (e.key === "ArrowDown") { var b = comboList.querySelector("button"); if (b) { e.preventDefault(); b.focus(); } }
      });
      comboList.addEventListener("click", function (e) {
        var b = e.target.closest("button[data-i]");
        if (!b || !comboList._items) return;
        comboAdd(comboList._items[+b.dataset.i]); comboSearch.value = ""; comboClose(); comboSearch.focus();
      });
      comboList.addEventListener("keydown", function (e) {
        var b = document.activeElement;
        if (e.key === "ArrowDown" && b.parentNode.nextElementSibling) { e.preventDefault(); b.parentNode.nextElementSibling.querySelector("button").focus(); }
        else if (e.key === "ArrowUp") { e.preventDefault(); var prev = b.parentNode.previousElementSibling; (prev ? prev.querySelector("button") : comboSearch).focus(); }
        else if (e.key === "Escape") { comboClose(); comboSearch.focus(); }
      });
      document.addEventListener("click", function (e) { if (!e.target.closest(".pf-combo-search")) comboClose(); });
    }
    if (comboBody) {
      comboBody.addEventListener("input", function (e) {
        if (!e.target.matches("[data-combo-qty]")) return;
        var i = +e.target.closest("tr").dataset.i; combo[i].qty = e.target.value; comboSync();
      });
      comboBody.addEventListener("click", function (e) {
        var b = e.target.closest("[data-combo-remove]");
        if (!b) return;
        combo.splice(+b.closest("tr").dataset.i, 1); comboRender();
      });
    }
    try { combo = JSON.parse((document.getElementById("pf-combo-rows") || {}).textContent || "[]"); } catch (err) { combo = []; }
    comboRender();

    /* ------------------------------------------------------------------ start */
    setMode(mode, true);
    applyType();
    showPreview(currentCode());
    if (isNew && !$('input[name="name"]').value) $('input[name="name"]').focus();
  });
})();
