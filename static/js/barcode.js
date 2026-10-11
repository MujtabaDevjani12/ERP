/* Self-contained barcode encoder (no external library, no network).
   Symbologies: code128, code39, ean13, ean8, upca, upce.

   App.barcode.svg(value, symbology, { height: 60, text: true }) -> { svg, error, text }
     svg    - markup of a responsive <svg> (width scales with its container), or "" when the value cannot be encoded
     error  - human readable reason when it cannot (e.g. "EAN-13 needs 12 or 13 digits")
     text   - the human-readable line (EAN/UPC get their check digit filled in)
   App.barcode.SYMBOLOGIES -> [{ value, label, hint }]
   App.barcode.checkDigit(digits) -> EAN/UPC modulo-10 check digit for the digits given */
(function () {
  "use strict";
  const App = (window.App = window.App || {});

  const SYMBOLOGIES = [
    { value: "code128", label: "Code 128", hint: "Any letters, numbers and symbols — the best default" },
    { value: "code39", label: "Code 39", hint: "Capital letters, numbers and - . space $ / + %" },
    { value: "ean13", label: "EAN-13", hint: "12 or 13 digits (retail standard)" },
    { value: "ean8", label: "EAN-8", hint: "7 or 8 digits (small packs)" },
    { value: "upca", label: "UPC-A", hint: "11 or 12 digits (North America)" },
    { value: "upce", label: "UPC-E", hint: "6 to 8 digits (compressed UPC)" },
  ];

  /* ---------------------------------------------------------------- Code 128 */
  // bar/space widths of the 107 symbols (0-102 data, 103-105 start A/B/C, 106 stop)
  const C128 = ["212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213", "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211", "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313", "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331", "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111", "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141", "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141", "114131", "311141", "411131", "211412", "211214", "211232", "2331112"];

  function code128(value) {
    if (!value) return { error: "Enter a value to encode" };
    for (const ch of value) { const c = ch.charCodeAt(0); if (c < 32 || c > 126) return { error: "Code 128 supports standard keyboard characters only" }; }
    const digitsOnly = /^\d+$/.test(value);
    const codes = []; let set;
    // an even run of digits packs two per symbol (set C); everything else uses set B
    if (digitsOnly && value.length >= 4) {
      set = "C";
      let rest = value;
      codes.push(105);
      if (rest.length % 2) { // odd count: start in B for the first digit, switch to C for the rest
        codes.length = 0; codes.push(104, value.charCodeAt(0) - 32, 99); rest = value.slice(1); set = "C";
      }
      for (let i = 0; i < rest.length; i += 2) codes.push(parseInt(rest.slice(i, i + 2), 10));
    } else {
      codes.push(104);
      for (const ch of value) codes.push(ch.charCodeAt(0) - 32);
    }
    let sum = codes[0]; for (let i = 1; i < codes.length; i++) sum += codes[i] * i;
    codes.push(sum % 103, 106);
    const widths = codes.map((c) => C128[c]).join("");
    return { widths, text: value };
  }

  /* ---------------------------------------------------------------- Code 39 */
  const C39 = { "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn", "4": "nnnwwnnnw", "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw", "8": "wnnwnnwnn", "9": "nnwwnnwnn", A: "wnnnnwnnw", B: "nnwnnwnnw", C: "wnwnnwnnn", D: "nnnnwwnnw", E: "wnnnwwnnn", F: "nnwnwwnnn", G: "nnnnnwwnw", H: "wnnnnwwnn", I: "nnwnnwwnn", J: "nnnnwwwnn", K: "wnnnnnnww", L: "nnwnnnnww", M: "wnwnnnnwn", N: "nnnnwnnww", O: "wnnnwnnwn", P: "nnwnwnnwn", Q: "nnnnnnwww", R: "wnnnnnwwn", S: "nnwnnnwwn", T: "nnnnwnwwn", U: "wwnnnnnnw", V: "nwwnnnnnw", W: "wwwnnnnnn", X: "nwnnwnnnw", Y: "wwnnwnnnn", Z: "nwwnwnnnn", "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn", "*": "nwnnwnwnn", "$": "nwnwnwnnn", "/": "nwnwnnnwn", "+": "nwnnnwnwn", "%": "nnnwnwnwn" };

  function code39(value) {
    const v = String(value || "").toUpperCase();
    if (!v) return { error: "Enter a value to encode" };
    for (const ch of v) if (!C39[ch] || ch === "*") return { error: `Code 39 cannot encode “${ch}” (capital letters, numbers and - . space $ / + % only)` };
    let widths = "";
    ("*" + v + "*").split("").forEach((ch, i) => {
      if (i) widths += "1"; // narrow gap between characters
      widths += C39[ch].split("").map((x) => (x === "w" ? "3" : "1")).join("");
    });
    return { widths, text: v };
  }

  /* ---------------------------------------------------------------- EAN / UPC */
  const L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"];
  const G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"];
  const R = L.map((c) => c.split("").map((b) => (b === "1" ? "0" : "1")).join(""));
  const EAN13_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"];
  const UPCE_PARITY = ["EEEOOO", "EEOEOO", "EEOOEO", "EEOOOE", "EOEEOO", "EOOEEO", "EOOOEE", "EOEOEO", "EOEOOE", "EOOEOE"]; // number system 0; system 1 is the inverse

  function checkDigit(digits) { // digits WITHOUT the check digit; weights 3,1,3,1… from the right
    let s = 0; const d = String(digits).split("").reverse();
    d.forEach((c, i) => { s += Number(c) * (i % 2 === 0 ? 3 : 1); });
    return (10 - (s % 10)) % 10;
  }
  const bitsToWidths = (bits) => { // "1011" -> run lengths, always starting with a bar
    let out = "", run = 1;
    for (let i = 1; i <= bits.length; i++) { if (bits[i] === bits[i - 1]) run++; else { out += run; run = 1; } }
    return bits[0] === "1" ? out : "0" + out;
  };

  function ean13(value) {
    if (!/^\d{12,13}$/.test(value)) return { error: "EAN-13 needs 12 or 13 digits" };
    const body = value.slice(0, 12), cd = checkDigit(body);
    if (value.length === 13 && Number(value[12]) !== cd) return { error: `Check digit should be ${cd}` };
    const full = body + cd, par = EAN13_PARITY[Number(full[0])];
    let bits = "101";
    for (let i = 0; i < 6; i++) bits += (par[i] === "L" ? L : G)[Number(full[i + 1])];
    bits += "01010";
    for (let i = 7; i < 13; i++) bits += R[Number(full[i])];
    bits += "101";
    return { bits, text: full, kind: "ean13" };
  }
  function ean8(value) {
    if (!/^\d{7,8}$/.test(value)) return { error: "EAN-8 needs 7 or 8 digits" };
    const body = value.slice(0, 7), cd = checkDigit(body);
    if (value.length === 8 && Number(value[7]) !== cd) return { error: `Check digit should be ${cd}` };
    const full = body + cd;
    let bits = "101";
    for (let i = 0; i < 4; i++) bits += L[Number(full[i])];
    bits += "01010";
    for (let i = 4; i < 8; i++) bits += R[Number(full[i])];
    bits += "101";
    return { bits, text: full, kind: "ean8" };
  }
  function upca(value) {
    if (!/^\d{11,12}$/.test(value)) return { error: "UPC-A needs 11 or 12 digits" };
    const r = ean13("0" + value);
    if (r.error) return { error: r.error.replace("Check digit", "Check digit") };
    return { bits: r.bits, text: r.text.slice(1), kind: "upca" };
  }
  function upceToUpca(sys, d) { // d = the 6 compressed digits
    const last = Number(d[5]);
    if (last <= 2) return sys + d.slice(0, 2) + d[5] + "0000" + d.slice(2, 5);
    if (last === 3) return sys + d.slice(0, 3) + "00000" + d.slice(3, 5);
    if (last === 4) return sys + d.slice(0, 4) + "00000" + d[4];
    return sys + d.slice(0, 5) + "0000" + d[5];
  }
  function upce(value) {
    if (!/^\d{6,8}$/.test(value)) return { error: "UPC-E needs 6 to 8 digits" };
    let sys = "0", d = value;
    if (value.length >= 7) { sys = value[0]; d = value.slice(1, 7); }
    if (sys !== "0" && sys !== "1") return { error: "UPC-E must start with 0 or 1" };
    const cd = checkDigit(upceToUpca(sys, d));
    if (value.length === 8 && Number(value[7]) !== cd) return { error: `Check digit should be ${cd}` };
    const par = UPCE_PARITY[cd].split("").map((p) => (sys === "1" ? (p === "E" ? "O" : "E") : p));
    let bits = "101";
    for (let i = 0; i < 6; i++) bits += (par[i] === "O" ? L : G)[Number(d[i])];
    bits += "010101";
    return { bits, text: sys + d + cd, kind: "upce" };
  }

  /* ---------------------------------------------------------------- rendering */
  const ENCODERS = { code128, code39, ean13, ean8, upca, upce };

  function svg(value, symbology, opt) {
    opt = opt || {};
    const enc = ENCODERS[symbology] || code128;
    const r = enc(String(value == null ? "" : value).trim());
    if (r.error) return { svg: "", error: r.error, text: "" };
    const widths = r.widths || bitsToWidths(r.bits);
    const quiet = 10, height = opt.height || 60, showText = opt.text !== false;
    let x = quiet, bars = "", barIdx = 0;
    for (const ch of widths) {
      const w = Number(ch);
      if (barIdx % 2 === 0 && w) bars += `M${x} 0h${w}v${height}h-${w}z`;
      x += w; barIdx++;
    }
    const total = x + quiet, textH = showText ? 16 : 0;
    const label = r.kind === "ean13" ? r.text[0] + "  " + r.text.slice(1, 7) + "  " + r.text.slice(7) : r.kind === "ean8" ? r.text.slice(0, 4) + "  " + r.text.slice(4) : r.text;
    const out = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${total} ${height + textH}" class="barcode-svg" role="img" aria-label="Barcode ${App.esc(r.text)}" preserveAspectRatio="xMidYMid meet"><rect width="${total}" height="${height + textH}" fill="#fff"/><path d="${bars}" fill="#000"/>${showText ? `<text x="${total / 2}" y="${height + 12}" text-anchor="middle" font-family="Inter,Arial,sans-serif" font-size="11" fill="#000" letter-spacing=".5">${App.esc(label)}</text>` : ""}</svg>`;
    return { svg: out, error: "", text: r.text };
  }

  App.barcode = { svg, SYMBOLOGIES, checkDigit, encode: (value, sym) => (ENCODERS[sym] || code128)(String(value || "").trim()) };
})();
