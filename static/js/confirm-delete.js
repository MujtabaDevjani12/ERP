/* Delete links open the ERP's normal "Are you sure?" pop-up (window.confirmDialog) instead of a separate confirm page.
 * A link whose address ends in /delete/ is deleted with a POST once the user confirms; data-no-confirm opts a link out.
 * Needs <form id="cdm-post" method="post" hidden>{% csrf_token %}</form> in the page (base.html has it).
 */
(function () {
  function rowName(el) {
    var host = el.closest("tr, [data-row]");
    var t = host && host.querySelector(".ui-cell-title, [data-row-name]");
    return t ? t.textContent.trim().replace(/\s+/g, " ").slice(0, 120) : "";
  }
  document.addEventListener("click", function (e) {
    var el = e.target.closest ? e.target.closest("a[href]") : null;
    if (!el || e.defaultPrevented || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;
    if (!/\/delete\/?$/.test(el.getAttribute("href") || "") || el.hasAttribute("data-no-confirm") || el.hasAttribute("data-confirm")) return;
    var form = document.getElementById("cdm-post");
    if (!form) return;                                           // no way to POST from here: let the old confirm page open
    e.preventDefault(); e.stopPropagation();
    var name = rowName(el);
    var go = function () { form.action = el.href; form.submit(); };
    var opts = { title: "Delete this record?", message: (name ? "“" + name + "” will be removed for good. " : "") + "Records that other documents depend on may be refused.", confirmLabel: "Delete", tone: "danger" };
    if (window.confirmDialog) { window.confirmDialog(opts).then(function (ok) { if (ok) go(); }); }
    else if (window.confirm(opts.title + "\n" + opts.message)) { go(); }
  }, true);

  /* bulk actions: <button type="submit" data-bulk-confirm="Delete {n} selected?"> inside a form with checkboxes named in data-bulk-name */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest ? e.target.closest("[data-bulk-confirm]") : null;
    if (!btn || btn.dataset.confirmed === "1") return;
    var form = btn.form; if (!form) return;
    var n = form.querySelectorAll('input[name="' + (btn.dataset.bulkName || "ids") + '"]:checked').length;
    e.preventDefault(); e.stopPropagation();
    if (!n) return;
    var opts = { title: btn.dataset.bulkConfirm.replace("{n}", n), message: btn.dataset.bulkText || "They will be removed for good. Anything other records depend on is skipped and kept.", confirmLabel: btn.dataset.bulkOk || "Delete", tone: "danger" };
    var go = function () { btn.dataset.confirmed = "1"; if (form.requestSubmit) form.requestSubmit(btn); else form.submit(); };
    if (window.confirmDialog) { window.confirmDialog(opts).then(function (ok) { if (ok) go(); }); }
    else if (window.confirm(opts.title)) { go(); }
  }, true);
})();
