"""Tiny HTML sanitiser for the product description editor: keeps basic formatting, drops everything else (scripts, styles, event
handlers, unknown tags / attributes, javascript: links). No third-party package needed."""
import re
from html import escape
from html.parser import HTMLParser

ALLOWED = {"p", "br", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li", "h2", "h3", "h4", "blockquote", "a", "hr", "div"}
VOID = {"br", "hr"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "math"}
SAFE_HREF = re.compile(r"^(https?://|mailto:|tel:|/)", re.I)
TAG_RE = re.compile(r"<[^>]+>")


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        if tag == "a":
            href = next((v for k, v in attrs if k == "href" and v), "").strip()
            if not SAFE_HREF.match(href):
                self.out.append("<a>")
            else:
                self.out.append(f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">')
        else:
            self.out.append(f"<{tag}>")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in VOID:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in ALLOWED or tag in VOID or tag not in self.stack:
            return
        while self.stack:                                         # close anything left open inside it
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(escape(data, quote=False))

    def result(self):
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_html(value):
    """The safe version of `value` (empty when the editor held nothing but empty paragraphs)."""
    value = value or ""
    if "<" not in value and "&" not in value:
        return value.strip()                                      # old plain-text description: keep as it is
    cleaner = _Cleaner()
    cleaner.feed(value)
    cleaner.close()
    html = cleaner.result().strip()
    return html if plain_text(html) or "<hr" in html else ""


def plain_text(value, limit=None):
    """Text only (for search snippets, the POS and exports)."""
    text = TAG_RE.sub(" ", (value or "").replace("<br>", " ").replace("</p>", " ").replace("</li>", " "))
    from html import unescape
    text = re.sub(r"\s+", " ", unescape(text)).strip()
    return text[:limit] if limit else text


from django import forms
from django.utils.safestring import mark_safe as _safe


class RichTextWidget(forms.Textarea):
    """A text box with a small toolbar (bold, italic, headings, lists, link...). Stores sanitised HTML in the field."""
    def __init__(self, attrs=None):
        super().__init__(attrs={"rows": 5, **(attrs or {})})

    def render(self, name, value, attrs=None, renderer=None):
        base = super().render(name, value, {**(attrs or {}), "hidden": True, "data-rte-input": "1"}, renderer)
        buttons = [("bold", "B", "Bold", ""), ("italic", "I", "Italic", ""), ("underline", "U", "Underline", ""), ("sep", "", "", ""),
                   ("h3", "H", "Heading", ""), ("p", "¶", "Normal text", ""), ("sep", "", "", ""),
                   ("ul", "•", "Bullet list", ""), ("ol", "1.", "Numbered list", ""), ("quote", "❝", "Quote", ""), ("sep", "", "", ""),
                   ("link", "🔗", "Add link", ""), ("clear", "✕", "Clear formatting", "")]
        bar = "".join('<span class="rte-sep"></span>' if k == "sep" else
                      f'<button type="button" class="rte-btn" data-cmd="{k}" title="{t}" aria-label="{t}">{label}</button>' for k, label, t, _ in buttons)
        return _safe(f'<div class="rte" data-rte><div class="rte-bar" role="toolbar" aria-label="Text formatting">{bar}</div>'
                     f'<div class="rte-area" contenteditable="true" role="textbox" aria-multiline="true" data-placeholder="Write a description - '
                     f'what it is, what it is made of, how to use it..."></div>{base}</div>')
