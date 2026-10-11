/* Barcode scanner support.
   Keyboard scanners (USB / Bluetooth "wedge") type the code very quickly and then press Enter or Tab. This module times the
   keystrokes so a scan can be told apart from a person typing, strips the configured prefix and beeps.
   Camera scanning uses the browser's BarcodeDetector (Chrome / Edge / Android); Safari and Firefox do not have it yet.

   App.scanner.configure(listOfScanners)   - active scanners from /api/v1/pos/config/ (first keyboard scanner is used)
   App.scanner.watch(inputEl, onScan)      - call onScan(code, {fast}) when Enter/Tab ends a fast burst typed into inputEl
   App.scanner.beep()
   App.scanner.camera({ onScan, title })   - opens a dialog with the live camera; resolves when closed
   App.scanner.test(inputEl, onResult)     - settings page tester: reports speed and verdict for each burst */
(function () {
  "use strict";
  const App = (window.App = window.App || {});
  let cfg = { prefix: "", suffix_key: "Enter", min_length: 4, max_gap_ms: 50, beep: true, any: false, camera: false };

  function configure(list) {
    const kb = (list || []).filter((s) => s.kind === "keyboard").sort((a, b) => b.is_default - a.is_default)[0];
    cfg = Object.assign({ prefix: "", suffix_key: "Enter", min_length: 4, max_gap_ms: 50, beep: true }, kb || {}, { any: !!kb, camera: (list || []).some((s) => s.kind === "camera") });
    return cfg;
  }

  let ctx;
  function beep() {
    if (cfg.beep === false) return;
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "square"; o.frequency.value = 1700; g.gain.value = 0.06; o.connect(g); g.connect(ctx.destination);
      o.start(); o.stop(ctx.currentTime + 0.08);
    } catch (e) { /* audio can be blocked until the first click - harmless */ }
  }

  /** Tracks keystroke timing on an input. Returns { stats() } where stats describes the burst that ended with the last Enter/Tab. */
  function tracker(input, onEnd) {
    let times = [], chars = "";
    const reset = () => { times = []; chars = ""; };
    input.addEventListener("keydown", (e) => {
      const now = performance.now();
      if (e.key === "Enter" || e.key === "Tab") {
        if (e.key === "Tab" && cfg.suffix_key !== "Tab") return;
        const gaps = times.slice(1).map((t, i) => t - times[i]);
        const avg = gaps.length ? gaps.reduce((a, b) => a + b, 0) / gaps.length : 0, worst = gaps.length ? Math.max(...gaps) : 0;
        const typed = input.value;
        const burst = { code: typed, chars: typed.length, avg, worst, fast: typed.length >= (cfg.min_length || 1) && gaps.length > 0 && worst <= Math.max(cfg.max_gap_ms * 2, 80) && avg <= cfg.max_gap_ms };
        const stop = onEnd(burst, e);
        reset();
        if (stop) e.preventDefault();
        return;
      }
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (times.length && now - times[times.length - 1] > 500) reset(); // a pause means a person is typing
        times.push(now); chars += e.key;
      } else if (e.key === "Backspace" || e.key === "Delete") reset();
    });
    input.addEventListener("input", () => { if (!input.value) reset(); });
  }

  function clean(code) {
    let c = String(code || "").trim();
    if (cfg.prefix && c.startsWith(cfg.prefix)) c = c.slice(cfg.prefix.length);
    return c;
  }

  /** onScan fires for Enter / Tab-terminated input; `fast` says whether it looked like a scanner. Returns true when handled. */
  function watch(input, onScan) {
    tracker(input, (burst, e) => {
      const code = clean(burst.code);
      if (!code) return false;
      if (e.key === "Tab" && !burst.fast) return false; // normal Tab navigation
      if (burst.fast) beep();
      onScan(code, { fast: burst.fast, avg: burst.avg });
      return e.key === "Tab";
    });
  }

  function test(input, onResult) {
    tracker(input, (burst) => { onResult(Object.assign({}, burst, { code: clean(burst.code), min: cfg.min_length, gap: cfg.max_gap_ms })); input.value = ""; return true; });
  }

  /* ----- camera ----- */
  const supportsCamera = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && "BarcodeDetector" in window);

  async function camera({ onScan, title = "Scan with camera" }) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { App.toast("warning", "Camera not available", "This browser cannot open the camera. Camera scanning needs HTTPS (or localhost)."); return; }
    if (!("BarcodeDetector" in window)) { App.toast("warning", "Camera scanning not supported", "Use Chrome or Edge, or a USB / Bluetooth scanner."); return; }
    let stream, stopped = false, last = "", lastAt = 0;
    const d = App.dialog({
      title: `<i class="bi bi-camera me-2"></i>${App.esc(title)}`, size: "modal-lg", scroll: false,
      body: `<div class="ratio ratio-4x3 bg-black rounded overflow-hidden"><video id="cam-video" playsinline muted autoplay></video></div><div class="text-muted small mt-2" id="cam-msg" aria-live="polite">Starting camera…</div>`,
      footer: `<button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Done</button>`,
    });
    const video = d.el.querySelector("#cam-video"), msg = d.el.querySelector("#cam-msg");
    d.el.addEventListener("hidden.bs.modal", () => { stopped = true; if (stream) stream.getTracks().forEach((t) => t.stop()); });
    try {
      const formats = (await BarcodeDetector.getSupportedFormats()).filter((f) => ["code_128", "code_39", "ean_13", "ean_8", "upc_a", "upc_e", "qr_code"].includes(f));
      const det = new BarcodeDetector({ formats });
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
      if (stopped) { stream.getTracks().forEach((t) => t.stop()); return; }
      video.srcObject = stream; await video.play(); msg.textContent = "Point the camera at a barcode.";
      const loop = async () => {
        if (stopped) return;
        try {
          const found = await det.detect(video);
          if (found.length) {
            const code = found[0].rawValue, now = Date.now();
            if (code && (code !== last || now - lastAt > 2500)) { last = code; lastAt = now; beep(); msg.textContent = `Scanned ${code}`; onScan(clean(code), { fast: true, camera: true }); }
          }
        } catch (e) { /* a frame failed - try the next */ }
        setTimeout(loop, 160);
      };
      loop();
    } catch (e) {
      msg.innerHTML = `<span class="text-danger">${App.esc(e && e.name === "NotAllowedError" ? "Camera permission was denied. Allow it in the browser address bar and try again." : e && e.name === "NotFoundError" ? "No camera found on this device." : "Could not start the camera: " + ((e && e.message) || e))}</span>`;
    }
  }

  App.scanner = { configure, watch, beep, camera, test, supportsCamera, get config() { return cfg; } };
})();
