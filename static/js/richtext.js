/* Small rich-text editor for <div data-rte> (see core/richtext.py RichTextWidget). Writes the HTML into the hidden textarea. */
(function () {
  function init(box) {
    if (box.dataset.ready) return; box.dataset.ready = "1";
    var area = box.querySelector(".rte-area"), input = box.querySelector("[data-rte-input]"), bar = box.querySelector(".rte-bar");
    area.innerHTML = input.value || "";
    if (input.value && input.value.indexOf("<") === -1) area.innerHTML = input.value.replace(/&/g, "&amp;").replace(/</g, "&lt;").split(/\n/).join("<br>");
    function sync() { var t = area.textContent.trim(); input.value = (t || area.querySelector("img,hr")) ? area.innerHTML : ""; }
    function state() {
      ["bold", "italic", "underline"].forEach(function (c) { var b = bar.querySelector('[data-cmd="' + c + '"]'); if (b) b.classList.toggle("on", document.queryCommandState(c)); });
    }
    area.addEventListener("input", sync); area.addEventListener("blur", sync);
    area.addEventListener("keyup", state); area.addEventListener("mouseup", state);
    area.addEventListener("paste", function (e) {                     // paste as plain text: no foreign fonts / colours
      e.preventDefault();
      var text = (e.clipboardData || window.clipboardData).getData("text/plain");
      document.execCommand("insertText", false, text);
    });
    bar.addEventListener("mousedown", function (e) { if (e.target.closest(".rte-btn")) e.preventDefault(); });   // keep the selection
    bar.addEventListener("click", function (e) {
      var b = e.target.closest(".rte-btn"); if (!b) return;
      area.focus();
      var c = b.dataset.cmd;
      if (c === "bold" || c === "italic" || c === "underline") document.execCommand(c);
      else if (c === "h3") document.execCommand("formatBlock", false, "h3");
      else if (c === "p") document.execCommand("formatBlock", false, "p");
      else if (c === "quote") document.execCommand("formatBlock", false, "blockquote");
      else if (c === "ul") document.execCommand("insertUnorderedList");
      else if (c === "ol") document.execCommand("insertOrderedList");
      else if (c === "clear") { document.execCommand("removeFormat"); document.execCommand("formatBlock", false, "p"); }
      else if (c === "link") {
        var url = window.prompt("Link address (https://...)", "https://");
        if (url && /^(https?:\/\/|mailto:|tel:)/i.test(url.trim())) document.execCommand("createLink", false, url.trim());
      }
      sync(); state();
    });
    var form = box.closest("form");
    if (form) form.addEventListener("submit", sync, true);
    sync();
  }
  function all() { document.querySelectorAll("[data-rte]").forEach(init); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", all); else all();
})();
