/* Receipt rendering + printing (80mm thermal friendly). Used by the POS and the sale detail page. */
(function () {
  "use strict";
  const App = window.App;
  const num = (v) => { const n = parseFloat(v); return Number.isFinite(n) ? n : 0; };
  const CSS = "body{font:12px/1.4 'Courier New',monospace;margin:0;padding:8px;width:var(--rw,76mm);color:#000}hr{border:0;border-top:1px dashed #000;margin:6px 0}table{width:100%;border-collapse:collapse}td,th{padding:2px 0;vertical-align:top}.text-end{text-align:right}.text-start{text-align:left}.text-center{text-align:center}.rc-sub{font-size:10px;color:#444}.rc-row{display:flex;justify-content:space-between}.rc-total{font-weight:bold;font-size:14px}.d-flex{display:flex}.justify-content-between{justify-content:space-between}.w-100{width:100%}.fs-6{font-size:14px}.mb-2{margin-bottom:6px}";

  function html(r) {
    const b = r.biller || {}, money = App.money;
    return `<div class="receipt"><div class="text-center mb-2">${b.logo_url && App.receipt.showLogo !== false ? `<img src="${App.esc(b.logo_url)}" alt="" style="max-width:40mm;max-height:18mm;display:block;margin:0 auto 4px">` : ""}<strong class="fs-6">${App.esc(b.name)}</strong>${b.header ? `<div>${App.esc(b.header)}</div>` : ""}<div>${App.esc(b.address || "")}</div><div>${App.esc(b.phone || "")}</div>${b.vat_no ? `<div>Tax no: ${App.esc(b.vat_no)}</div>` : ""}</div>
      <hr><div>${App.esc(r.reference)}</div><div>${App.datetime(r.date)}</div><div>Customer: ${App.esc(r.customer.name)}</div><div>Cashier: ${App.esc(r.cashier)}</div><hr>
      <table class="w-100"><thead><tr><th class="text-start">Item</th><th class="text-end" style="padding-left:10px">Qty</th><th class="text-end" style="padding-left:10px">Amount</th></tr></thead><tbody>${r.items.map((i) => `<tr><td>${App.esc(i.name)}<div class="rc-sub">${App.money(i.net_unit_price)} each${num(i.discount) ? ` · disc ${App.money(i.discount)}` : ""}</div></td><td class="text-end" style="padding-left:10px">${App.qty(i.quantity)}</td><td class="text-end" style="padding-left:10px">${App.num(i.subtotal)}</td></tr>`).join("")}</tbody></table><hr>
      <div class="rc-row"><span>Subtotal</span><span>${money(r.total)}</span></div>${num(r.order_discount) ? `<div class="rc-row"><span>Discount</span><span>−${money(r.order_discount)}</span></div>` : ""}<div class="rc-row"><span>Tax</span><span>${money(num(r.product_tax) + num(r.order_tax))}</span></div>${num(r.shipping) ? `<div class="rc-row"><span>Shipping</span><span>${money(r.shipping)}</span></div>` : ""}${num(r.rounding) ? `<div class="rc-row"><span>Rounding</span><span>${money(r.rounding)}</span></div>` : ""}
      <div class="rc-row rc-total"><span>TOTAL</span><span>${money(r.payable)}</span></div><hr>${r.payments.map((p) => `<div class="rc-row"><span>${App.esc(p.method)}</span><span>${money(num(p.tendered) ? p.tendered : p.amount)}</span></div>${num(p.change) ? `<div class="rc-row"><span>Change</span><span>${money(p.change)}</span></div>` : ""}`).join("")}${num(r.balance) > 0 ? `<div class="rc-row"><strong>Balance due</strong><strong>${money(r.balance)}</strong></div>` : ""}
      <hr><div class="text-center">${App.esc(b.footer || "Thank you for shopping with us!")}</div></div>`;
  }

  function print(content) {
    const f = document.createElement("iframe"); f.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0"; document.body.appendChild(f);
    let done = false; const go = () => { if (done) return; done = true; f.contentWindow.focus(); f.contentWindow.print(); setTimeout(() => f.remove(), 1500); };
    f.contentDocument.open(); f.contentDocument.write(`<!doctype html><html><head><title>Receipt</title><style>${CSS}@page{margin:0;size:${App.receipt.width || 80}mm auto}:root{--rw:${(App.receipt.width || 80) - 4}mm}</style></head><body>${content}</body></html>`); f.contentDocument.close();
    f.onload = go; setTimeout(go, 400);
  }

  /* ----- choose where a receipt goes: the default receipt printer from Settings, or this computer's print dialog ----- */
  let printers = [];
  function setPrinters(list) {
    printers = list || [];
    const p = defaultPrinter();
    if (p) { App.receipt.width = p.paper_width_mm; App.receipt.showLogo = p.print_logo; }
  }
  function defaultPrinter() { return printers.find((p) => p.is_default) || printers[0] || null; }

  /** Print a finished sale. Network printers are driven by the server (ESC/POS); otherwise the browser print dialog opens. */
  async function printSale(receipt, saleId) {
    const p = defaultPrinter();
    if (p && p.connection === "network" && saleId) {
      try { const r = await App.api.post(`/api/v1/printers/${p.id}/print-sale/`, { sale: saleId }); App.toast("success", "Printing", r.message); return true; }
      catch (e) { App.toast("danger", "Could not print on " + p.name, e.message + " Opening the print dialog instead."); }
    }
    print(html(receipt));
    return true;
  }

  App.receipt = { html, print, printSale, setPrinters, defaultPrinter, width: 80, showLogo: true };
})();
