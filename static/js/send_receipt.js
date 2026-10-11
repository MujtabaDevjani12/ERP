/* Send a receipt by email / WhatsApp and record customer feedback. Used by the POS "sale complete" window and the sale page.

   App.sendReceipt(sale, { channel: "email" | "whatsapp", template: "sale_receipt" })
   App.feedbackDialog(sale, { onSaved(feedback) })
   `sale` needs: id, reference, customer_email, customer_phone (the sale detail / checkout response has them). */
(function () {
  "use strict";
  const App = (window.App = window.App || {}), A = "/api/v1/";

  async function sendReceipt(sale, opts = {}) {
    let tpl;
    try { tpl = await App.api.get(`${A}message-templates/`, { is_active: true }); } catch (e) { App.toastError(e); return; }
    const want = opts.template || "sale_receipt";
    const options = (ch) => tpl.filter((t) => t.channel === ch).map((t) => `<option value="${t.id}" ${t.key === want ? "selected" : ""}>${App.esc(t.name)}</option>`).join("") || '<option value="">No active templates</option>';
    let ch = opts.channel === "whatsapp" ? "whatsapp" : "email";
    const d = App.dialog({
      title: '<i class="bi bi-send me-2"></i>Send to customer', size: "dialog-compact", scroll: false,
      body: `<div class="seg mb-3" role="tablist" aria-label="Send by"><button class="seg-btn" data-ch="email" type="button" role="tab"><i class="bi bi-envelope"></i>Email</button><button class="seg-btn" data-ch="whatsapp" type="button" role="tab"><i class="bi bi-whatsapp"></i>WhatsApp</button></div>
        <form id="sf" novalidate>
          <div data-pane="email"><label class="form-label" for="s-to">Email address</label><input class="form-control mb-3" id="s-to" name="to" type="email" value="${App.esc(sale.customer_email || "")}" placeholder="customer@example.com" autocomplete="off">
            <label class="form-label" for="s-tpl-e">Message</label><select class="form-select mb-3" id="s-tpl-e" name="template_email">${options("email")}</select>
            <div class="form-check"><input class="form-check-input" type="checkbox" id="s-pdf-e" name="pdf_email" checked><label class="form-check-label" for="s-pdf-e"><i class="bi bi-file-earmark-pdf me-1"></i>Attach the receipt as a PDF</label></div></div>
          <div data-pane="whatsapp"><label class="form-label" for="s-ph">Phone number</label><input class="form-control mb-3" id="s-ph" name="phone" value="${App.esc(sale.customer_phone || "")}" placeholder="0300 1234567" inputmode="tel" autocomplete="off">
            <label class="form-label" for="s-tpl-w">Message</label><select class="form-select" id="s-tpl-w" name="template_whatsapp">${options("whatsapp")}</select>
            <div class="form-check mt-3"><input class="form-check-input" type="checkbox" id="s-pdf-w" name="pdf_whatsapp" checked><label class="form-check-label" for="s-pdf-w"><i class="bi bi-file-earmark-pdf me-1"></i>Include a link to the receipt PDF</label></div>
            <div class="form-text">WhatsApp opens with the message ready. You press Send there. The PDF link works for 30 days.</div></div>
        </form>`,
      footer: '<button class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button><button class="btn btn-primary" id="s-go"><i class="bi bi-send"></i><span>Send email</span></button>',
    });
    const f = d.el.querySelector("#sf"), go = d.el.querySelector("#s-go");
    const show = () => {
      App.clearErrors(f);
      d.el.querySelectorAll("[data-ch]").forEach((x) => { const on = x.dataset.ch === ch; x.classList.toggle("on", on); x.setAttribute("aria-selected", on); });
      d.el.querySelectorAll("[data-pane]").forEach((p) => p.classList.toggle("d-none", p.dataset.pane !== ch));
      go.querySelector("span").textContent = ch === "email" ? "Send email" : "Open WhatsApp";
      setTimeout(() => f.querySelector(ch === "email" ? "#s-to" : "#s-ph").focus(), 60);
    };
    d.el.querySelectorAll("[data-ch]").forEach((b) => b.addEventListener("click", () => { ch = b.dataset.ch; show(); }));
    show();
    const run = () => App.busy(go, async () => {
      App.clearErrors(f);
      try {
        if (ch === "email") {
          const r = await App.api.post(`${A}messaging/send-email/`, { sale: sale.id, to: f.to.value.trim(), template: f.template_email.value, attach_pdf: f.pdf_email.checked });
          d.close(); App.toast("success", "Email sent", `Sent to ${r.to}${r.pdf ? " with the receipt PDF" : ""}.`);
        } else {
          const r = await App.api.post(`${A}messaging/whatsapp-link/`, { sale: sale.id, phone: f.phone.value.trim(), template: f.template_whatsapp.value, attach_pdf: f.pdf_whatsapp.checked });
          window.open(r.url, "_blank", "noopener"); d.close();
          if (r.pdf_link_private) App.toast("warning", "PDF link only works on this network", "The receipt link points to this computer, so the customer cannot open it. Set PUBLIC_BASE_URL to your public address.", 12000);
          else App.toast("success", "WhatsApp opened", "Check the message and press Send.");
        }
        if (opts.onSent) opts.onSent(ch);
      } catch (err) { App.showErrors(f, err); }
    });
    go.addEventListener("click", run);
    f.addEventListener("submit", (e) => { e.preventDefault(); run(); });
  }

  const LABELS = ["", "Poor", "Fair", "Good", "Very good", "Excellent"];
  async function feedbackDialog(sale, opts = {}) {
    let fb = null;
    try { fb = await App.api.get(`${A}messaging/feedback/`, { sale: sale.id }); } catch (e) { App.toastError(e); return; }
    let rating = fb ? fb.rating : 0;
    const d = App.dialog({
      title: '<i class="bi bi-chat-heart me-2"></i>Customer feedback', size: "dialog-compact", scroll: false,
      body: `<p class="text-muted mb-3">How was the customer's experience? <span class="text-nowrap">${App.esc(sale.reference || "")}</span></p>
        <form id="ff" novalidate>
          <div class="stars" role="radiogroup" aria-label="Rating">${[1, 2, 3, 4, 5].map((n) => `<button type="button" class="star" role="radio" data-n="${n}" aria-label="${n} star${n > 1 ? "s" : ""}"><i class="bi bi-star"></i></button>`).join("")}</div>
          <div class="text-center fw-semibold mb-3 stars-label" id="star-label" aria-live="polite">&nbsp;</div>
          <label class="form-label" for="f-comment">Comment <span class="text-muted fw-normal">(optional)</span></label>
          <textarea class="form-control" id="f-comment" name="comment" rows="3" maxlength="500" placeholder="What did the customer say?">${App.esc(fb ? fb.comment : "")}</textarea>
          <div class="d-flex flex-wrap align-items-center gap-2 mt-3 pt-3 border-top"><span class="text-muted small me-auto">Not here? Ask them to reply:</span>
            <button type="button" class="btn btn-sm btn-outline-secondary" data-ask="whatsapp"><i class="bi bi-whatsapp"></i>WhatsApp</button><button type="button" class="btn btn-sm btn-outline-secondary" data-ask="email"><i class="bi bi-envelope"></i>Email</button></div>
        </form>`,
      footer: '<button class="btn btn-outline-secondary" data-bs-dismiss="modal">Skip</button><button class="btn btn-primary" id="ff-save"><i class="bi bi-check2"></i>Save feedback</button>',
    });
    const stars = d.el.querySelectorAll(".star"), label = d.el.querySelector("#star-label");
    const paint = (hover) => {
      const v = hover || rating;
      stars.forEach((s) => { const on = Number(s.dataset.n) <= v; s.classList.toggle("on", on); s.setAttribute("aria-checked", Number(s.dataset.n) === rating); s.querySelector("i").className = `bi bi-star${on ? "-fill" : ""}`; });
      label.textContent = v ? LABELS[v] : " ";
    };
    stars.forEach((s) => { s.addEventListener("click", () => { rating = Number(s.dataset.n); paint(); }); s.addEventListener("mouseenter", () => paint(Number(s.dataset.n))); s.addEventListener("mouseleave", () => paint()); });
    d.el.querySelector(".stars").addEventListener("keydown", (e) => { if (/^[1-5]$/.test(e.key)) { rating = Number(e.key); paint(); } });
    paint();
    d.el.querySelectorAll("[data-ask]").forEach((b) => b.addEventListener("click", () => { d.close(); sendReceipt(sale, { channel: b.dataset.ask, template: "feedback_request" }); }));
    const save = d.el.querySelector("#ff-save");
    save.addEventListener("click", () => {
      if (!rating) { App.toast("warning", "Choose a rating", "Tap 1 to 5 stars first."); return; }
      App.busy(save, async () => {
        try {
          const r = await App.api.post(`${A}messaging/feedback/`, { sale: sale.id, rating, comment: d.el.querySelector("#f-comment").value.trim() });
          d.close(); App.toast("success", "Feedback saved", `${rating} of 5 — thank you.`); if (opts.onSaved) opts.onSaved(r);
        } catch (err) { App.showErrors(d.el.querySelector("#ff"), err); }
      });
    });
    setTimeout(() => stars[0].focus(), 250);
  }

  /* Open the receipt PDF for a sale in a new tab (the browser's viewer lets the cashier print or save it). */
  App.openReceiptPdf = (saleId) => window.open(`${A}messaging/receipt-pdf/?sale=${encodeURIComponent(saleId)}`, "_blank", "noopener");

  App.sendReceipt = sendReceipt;
  App.feedbackDialog = feedbackDialog;
})();
