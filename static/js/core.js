/* Core UI toolkit: API client, formatters, toasts, confirm dialogs, states, theme, shell.
   Classic script (no modules) exposing a single global: window.App */
(function () {
  "use strict";

  const App = (window.App = window.App || {});
  const cfgEl = document.getElementById("app-config");
  App.config = cfgEl ? JSON.parse(cfgEl.textContent) : {};
  const C = App.config;

  /* ------------------------------------------------------------- utils */
  App.esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  App.debounce = (fn, ms = 250) => { let t; const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; d.cancel = () => clearTimeout(t); return d; };
  App.qs = (sel, root = document) => root.querySelector(sel);
  App.qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  App.uuid = () => (crypto.randomUUID ? crypto.randomUUID() : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => { const r = (Math.random() * 16) | 0; return (c === "x" ? r : (r & 0x3) | 0x8).toString(16); }));
  App.store = {
    get(k, d) { try { const v = localStorage.getItem("pos:" + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("pos:" + k, JSON.stringify(v)); } catch (e) { /* storage may be blocked */ } },
  };
  App.can = (code) => !!(C.user && (C.user.permissions || []).includes(code)) || !!(C.user && (C.user.is_owner || C.user.is_admin) && !/delete/.test(code));
  App.cookie = (name) => { const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)")); return m ? decodeURIComponent(m[1]) : ""; };

  /* ------------------------------------------------------------- formatting */
  const dec = () => (C.decimals != null ? C.decimals : 2);
  const nfCache = {};
  function nf(d) { return nfCache[d] || (nfCache[d] = new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })); }
  App.num = (v, d) => { const n = Number(v); return Number.isFinite(n) ? nf(d == null ? dec() : d).format(n) : "—"; };
  App.qty = (v) => { const n = Number(v); return Number.isFinite(n) ? nf(Math.min(C.qty_decimals != null ? C.qty_decimals : 2, 4)).format(n).replace(/\.?0+$/, "") : "—"; };
  App.money = (v, d) => {
    const n = Number(v); if (!Number.isFinite(n)) return "—";
    const txt = nf(d == null ? dec() : d).format(n), sym = C.currency || "";
    return (C.currency_position === "after" ? `${txt} ${sym}` : `${sym} ${txt}`).trim();
  };
  App.compact = (v) => { const n = Number(v); return Number.isFinite(n) ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n) : "—"; };
  /* a bare YYYY-MM-DD is a calendar day, not a UTC instant */
  App.date = (v) => { if (!v) return "—"; const d = /^\d{4}-\d{2}-\d{2}$/.test(v) ? new Date(+v.slice(0, 4), +v.slice(5, 7) - 1, +v.slice(8, 10)) : new Date(v); return isNaN(d) ? "—" : d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }); };
  App.datetime = (v) => { if (!v) return "—"; const d = new Date(v); return isNaN(d) ? "—" : d.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }); };
  App.initials = (name) => String(name || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  App.round = (x, d = 4) => { const f = Math.pow(10, d); return Math.round((Number(x) + Number.EPSILON) * f) / f; };

  const STATUS = {
    paid: ["success", "check-circle-fill", "Paid"], partial: ["warning", "circle-half", "Partial"], pending: ["secondary", "clock", "Unpaid"], due: ["danger", "exclamation-circle-fill", "Overdue"],
    completed: ["success", "check-circle-fill", "Completed"], received: ["success", "check-circle-fill", "Received"], ordered: ["info", "truck", "Ordered"],
    sent: ["info", "send", "Sent"], returned: ["warning", "arrow-return-left", "Returned"], open: ["success", "unlock", "Open"], closed: ["secondary", "lock", "Closed"],
    active: ["success", "dot", "Active"], inactive: ["secondary", "dot", "Inactive"],
    packing: ["warning", "box-seam", "Packing"], on_the_way: ["info", "truck", "On the way"], delivered: ["success", "check-circle-fill", "Delivered"], cancelled: ["secondary", "x-circle", "Cancelled"],
  };
  App.badge = (status, label) => { const s = STATUS[status] || ["secondary", "dot", String(status || "—")]; return `<span class="badge badge-soft-${s[0]}"><i class="bi bi-${s[1]}" aria-hidden="true"></i>${App.esc(label || s[2])}</span>`; };
  App.yesNo = (v) => (v ? '<span class="badge badge-soft-success">Yes</span>' : '<span class="badge badge-soft-secondary">No</span>');

  /* ------------------------------------------------------------- API client */
  class ApiError extends Error {
    constructor(message, { status = 0, code = "error", fields = {} } = {}) { super(message); this.name = "ApiError"; this.status = status; this.code = code; this.fields = fields || {}; }
  }
  App.ApiError = ApiError;

  function buildUrl(url, params) {
    if (!params) return url;
    const u = new URL(url, window.location.origin);
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") u.searchParams.set(k, v); });
    return u.pathname + u.search;
  }

  /** App.api("POST", "/api/v1/x/", {body, params, headers, signal}) -> parsed JSON (or null for 204). Throws ApiError. */
  App.api = async function (method, url, opts = {}) {
    const headers = Object.assign({ Accept: "application/json", "X-Requested-With": "XMLHttpRequest" }, opts.headers || {});
    const init = { method, headers, credentials: "same-origin", signal: opts.signal };
    if (method !== "GET" && method !== "HEAD") headers["X-CSRFToken"] = App.cookie("csrftoken");
    if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    if (opts.form) { delete headers["Content-Type"]; init.body = opts.form; }
    let res;
    try { res = await fetch(buildUrl(url, opts.params), init); }
    catch (e) {
      if (e.name === "AbortError") throw e;
      throw new ApiError("Cannot reach the server. Check your connection and try again.", { code: "network" });
    }
    if (res.status === 204) return null;
    let data = null;
    const text = await res.text();
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    if (res.ok) return data;
    if (res.status === 401) { App.sessionExpired(); }
    const err = (data && data.error) || {};
    let message = err.message || (data && data.detail) || "";
    if (!message) message = res.status >= 500 ? "Something went wrong on the server. Please try again." : res.status === 403 ? "You do not have permission to do this." : res.status === 404 ? "Not found." : "The request could not be completed.";
    throw new ApiError(message, { status: res.status, code: err.code || (res.status === 403 ? "forbidden" : res.status >= 500 ? "server" : "error"), fields: err.fields || {} });
  };
  ["get", "post", "patch", "put", "delete"].forEach((m) => { App.api[m] = (url, body, opts = {}) => App.api(m.toUpperCase(), url, m === "get" ? Object.assign({ params: body }, opts) : Object.assign({ body }, opts)); });
  App.sessionExpired = () => { if (App._expired) return; App._expired = true; App.toast("warning", "Session expired", "Please sign in again."); setTimeout(() => { window.location.href = "/login/?next=" + encodeURIComponent(location.pathname + location.search); }, 1200); };
  App.fetchAll = async function (url, params = {}) { // follows pagination for small lookup lists
    const out = []; let page = 1;
    for (;;) {
      const d = await App.api.get(url, Object.assign({ page_size: 200, page }, params));
      out.push(...(d.results || d)); if (!d.next || !d.results) break; page += 1; if (page > 25) break;
    }
    return out;
  };

  /* ------------------------------------------------------------- toasts */
  let stack;
  const ICONS = { success: "check-circle-fill", danger: "x-octagon-fill", warning: "exclamation-triangle-fill", info: "info-circle-fill" };
  App.toast = function (type, title, message, ms) {
    if (!stack) { stack = document.createElement("div"); stack.className = "toast-stack"; stack.setAttribute("aria-live", "polite"); stack.setAttribute("aria-atomic", "false"); document.body.appendChild(stack); }
    const el = document.createElement("div");
    el.className = `app-toast ${type}`; el.setAttribute("role", type === "danger" ? "alert" : "status");
    el.innerHTML = `<i class="bi bi-${ICONS[type] || ICONS.info} t-icon" aria-hidden="true"></i><div class="flex-grow-1"><div class="t-title">${App.esc(title)}</div>${message ? `<div class="t-msg">${App.esc(message)}</div>` : ""}</div><button type="button" class="btn-close btn-sm" aria-label="Dismiss"></button>`;
    const close = () => { el.classList.add("leaving"); setTimeout(() => el.remove(), 180); };
    el.querySelector(".btn-close").addEventListener("click", close);
    stack.appendChild(el);
    while (stack.children.length > 4) stack.firstChild.remove();
    setTimeout(close, ms || (type === "danger" ? 7000 : 4000));
  };
  App.toastError = (e, title = "Something went wrong") => { if (e && e.name === "AbortError") return; App.toast("danger", title, e && e.message ? e.message : String(e)); };

  /* ------------------------------------------------------------- modals */
  App.modal = function (el) { return bootstrap.Modal.getOrCreateInstance(el); };
  /** Build a one-off modal from HTML, returns {el, modal, close}. Removed from DOM when hidden. */
  App.dialog = function ({ title, body, footer = "", size = "", id, static: isStatic = false, scroll = true, onShown }) {
    const el = document.createElement("div");
    el.className = "modal fade"; el.tabIndex = -1; el.setAttribute("aria-hidden", "true");
    if (id) el.id = id;
    el.innerHTML = `<div class="modal-dialog ${size} ${scroll ? "modal-dialog-scrollable" : ""} modal-dialog-centered"><div class="modal-content">
      <div class="modal-header"><h2 class="modal-title" id="m${Date.now()}">${title}</h2><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button></div>
      <div class="modal-body">${body || ""}</div>${footer ? `<div class="modal-footer">${footer}</div>` : ""}</div></div>`;
    el.setAttribute("aria-labelledby", el.querySelector(".modal-title").id);
    document.body.appendChild(el);
    const modal = new bootstrap.Modal(el, isStatic ? { backdrop: "static", keyboard: false } : {});
    el.addEventListener("hidden.bs.modal", () => { modal.dispose(); el.remove(); });
    if (onShown) el.addEventListener("shown.bs.modal", () => onShown(el));
    modal.show();
    return { el, modal, close: () => modal.hide() };
  };

  /** Promise-based confirmation. tone: danger | warning | primary */
  App.confirm = function ({ title = "Are you sure?", message = "", confirmText = "Confirm", cancelText = "Cancel", tone = "danger", icon, requireText } = {}) {
    return new Promise((resolve) => {
      const ic = icon || (tone === "danger" ? "trash3" : tone === "warning" ? "exclamation-triangle" : "question-circle");
      const d = App.dialog({
        title: "", size: "modal-sm", scroll: false,
        body: `<div class="text-center"><div class="confirm-icon ${tone}"><i class="bi bi-${ic}" aria-hidden="true"></i></div><h3 class="h5 mb-2">${App.esc(title)}</h3><p class="text-muted mb-0">${message}</p>${requireText ? `<input class="form-control mt-3" data-confirm-text placeholder="Type ${App.esc(requireText)} to confirm" aria-label="Confirmation text">` : ""}</div>`,
        footer: `<button type="button" class="btn btn-outline-secondary flex-fill" data-bs-dismiss="modal">${App.esc(cancelText)}</button><button type="button" class="btn btn-${tone === "primary" ? "primary" : tone} flex-fill" data-ok ${requireText ? "disabled" : ""}>${App.esc(confirmText)}</button>`,
      });
      d.el.querySelector(".modal-header").classList.add("border-0", "pb-0");
      let result = false;
      const ok = d.el.querySelector("[data-ok]");
      const input = d.el.querySelector("[data-confirm-text]");
      if (input) input.addEventListener("input", () => { ok.disabled = input.value.trim() !== requireText; });
      ok.addEventListener("click", () => { result = true; d.close(); });
      d.el.addEventListener("hidden.bs.modal", () => resolve(result));
      d.el.addEventListener("shown.bs.modal", () => (input || ok).focus());
    });
  };

  /* ------------------------------------------------------------- states */
  App.skeletonRows = (rows = 6, cols = 5) => Array.from({ length: rows }, () => `<tr>${Array.from({ length: cols }, (_, i) => `<td><div class="skeleton skeleton-line ${i === 0 ? "w-75" : ""}"></div></td>`).join("")}</tr>`).join("");
  App.skeletonCards = (n = 4) => Array.from({ length: n }, () => `<div class="col"><div class="kpi"><div class="skeleton" style="width:40px;height:40px;border-radius:12px"></div><div class="skeleton skeleton-line w-50 mt-3"></div><div class="skeleton" style="height:26px;width:70%;margin:8px 0"></div><div class="skeleton skeleton-line w-75"></div></div></div>`).join("");
  App.emptyState = ({ icon = "inbox", title = "Nothing here yet", text = "", action = "" } = {}) => `<div class="state-box"><div class="state-icon"><i class="bi bi-${icon}" aria-hidden="true"></i></div><h3>${App.esc(title)}</h3>${text ? `<p>${App.esc(text)}</p>` : ""}${action}</div>`;
  App.errorState = (err, retryLabel = "Try again") => {
    const code = err && err.code, status = err && err.status;
    const icon = code === "network" ? "wifi-off" : status === 403 ? "shield-lock" : status >= 500 ? "bug" : "exclamation-triangle";
    const title = code === "network" ? "You appear to be offline" : status === 403 ? "No permission" : status >= 500 ? "Server error" : "Could not load";
    return `<div class="state-box error" role="alert"><div class="state-icon"><i class="bi bi-${icon}" aria-hidden="true"></i></div><h3>${title}</h3><p>${App.esc((err && err.message) || "Please try again.")}</p>${status === 403 ? "" : `<button type="button" class="btn btn-outline-secondary btn-sm" data-retry><i class="bi bi-arrow-clockwise"></i>${retryLabel}</button>`}</div>`;
  };
  /** Disable a button and show a spinner while `fn` runs. */
  App.busy = async function (btn, fn) {
    if (!btn) return fn();
    const html = btn.innerHTML, w = btn.offsetWidth;
    btn.disabled = true; btn.style.minWidth = w + "px";
    btn.innerHTML = '<span class="spinner-border" role="status" aria-hidden="true"></span><span class="visually-hidden">Working…</span>';
    try { return await fn(); } finally { btn.disabled = false; btn.innerHTML = html; btn.style.minWidth = ""; }
  };

  /* ------------------------------------------------------------- forms */
  App.clearErrors = (form) => { App.qsa(".is-invalid", form).forEach((e) => e.classList.remove("is-invalid")); App.qsa(".invalid-feedback.dyn", form).forEach((e) => e.remove()); const a = App.qs(".form-alert", form); if (a) a.remove(); };
  App.showErrors = function (form, err) {
    App.clearErrors(form);
    let first = null; const general = [];
    Object.entries(err.fields || {}).forEach(([name, msgs]) => {
      const input = form.querySelector(`[name="${name}"]`);
      const text = Array.isArray(msgs) ? msgs.join(" ") : typeof msgs === "string" ? msgs : JSON.stringify(msgs);
      if (!input) { general.push(`${name.replace(/_/g, " ")}: ${text}`); return; }
      input.classList.add("is-invalid"); input.setAttribute("aria-invalid", "true");
      const fb = document.createElement("div"); fb.className = "invalid-feedback dyn" + (input.closest(".input-group") ? " d-block" : ""); fb.textContent = text; // inside an input group the message is not the input's sibling, so show it explicitly
      (input.closest(".input-group") || input).insertAdjacentElement("afterend", fb);
      if (!first) first = input;
    });
    if (!first || general.length || !Object.keys(err.fields || {}).length) {
      const a = document.createElement("div"); a.className = "alert alert-danger form-alert d-flex gap-2"; a.setAttribute("role", "alert");
      a.innerHTML = `<i class="bi bi-exclamation-triangle-fill"></i><div>${App.esc(err.message || "Please check the form.")}${general.length ? "<br>" + general.map(App.esc).join("<br>") : ""}</div>`;
      form.prepend(a);
    }
    if (first) first.focus();
  };

  /* ------------------------------------------------------------- charts */
  App.chartColors = () => {
    const dark = document.documentElement.getAttribute("data-bs-theme") === "dark";
    const css = getComputedStyle(document.documentElement);
    return { primary: css.getPropertyValue("--app-primary").trim() || "#ea580c", ink: css.getPropertyValue("--app-ink").trim() || (dark ? "#f3efe9" : "#1a1816"), success: "#10b981", warning: "#f59e0b", danger: "#ef4444", info: "#0ea5e9", violet: "#8b5cf6", grid: dark ? "rgba(255,255,255,.07)" : "rgba(40,30,20,.07)", text: dark ? "#a8a198" : "#6f6a63", palette: ["#ea580c", (dark ? "#f3efe9" : "#1a1816"), "#10b981", "#0ea5e9", "#8b5cf6", "#f59e0b", "#14b8a6", "#ef4444"] };
  };

  /* ------------------------------------------------------------- theme & shell */
  const currentTheme = () => (document.documentElement.getAttribute("data-bs-theme") === "dark" ? "dark" : "light");
  function syncThemeUi() {
    const t = currentTheme();
    App.qsa("[data-theme-icon]").forEach((i) => { i.className = "bi bi-" + (t === "dark" ? "sun" : "moon-stars"); });
    App.qsa("[data-theme-label]").forEach((l) => { l.textContent = t === "dark" ? "Light mode" : "Dark mode"; });
    App.qsa("[data-theme-set]").forEach((b) => { const on = b.dataset.themeSet === t; b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); });
  }
  App.setTheme = function (t) {
    if (t === currentTheme()) { syncThemeUi(); return; }
    document.documentElement.setAttribute("data-bs-theme", t); App.store.set("theme", t); syncThemeUi();
    document.dispatchEvent(new CustomEvent("themechange", { detail: t }));
  };
  App.toggleTheme = function () { App.setTheme(currentTheme() === "dark" ? "light" : "dark"); };

  document.addEventListener("DOMContentLoaded", () => {
    syncThemeUi();
    App.qsa("[data-theme-toggle]").forEach((b) => b.addEventListener("click", App.toggleTheme));
    App.qsa("[data-theme-set]").forEach((b) => b.addEventListener("click", () => App.setTheme(b.dataset.themeSet)));
    const sb = App.qs(".sidebar"), bd = App.qs(".sidebar-backdrop");
    const setSb = (open) => { if (!sb) return; sb.classList.toggle("open", open); if (bd) bd.classList.toggle("show", open); };
    App.qsa("[data-sidebar-toggle]").forEach((b) => b.addEventListener("click", () => setSb(!sb.classList.contains("open"))));
    if (bd) bd.addEventListener("click", () => setSb(false));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setSb(false); });
    App.qsa("[data-logout]").forEach((b) => b.addEventListener("click", async (e) => { e.preventDefault(); const f = document.getElementById("logout-form"); if (f) f.submit(); }));
    // connectivity banner
    const banner = document.createElement("div"); banner.className = "offline-banner"; banner.setAttribute("role", "status"); banner.textContent = "You are offline. Changes will not be saved until the connection returns."; document.body.appendChild(banner);
    const upd = () => banner.classList.toggle("show", !navigator.onLine); window.addEventListener("online", upd); window.addEventListener("offline", upd); upd();
    // tooltips
    App.qsa("[data-bs-toggle='tooltip']").forEach((el) => new bootstrap.Tooltip(el));
  });
})();
