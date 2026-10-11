"""Round 16: dialogs must sit above their backdrop, pop-out lists must not be clipped, ERP lookup list is script-positioned."""
from pathlib import Path
from unittest import mock

from django.template.loader import render_to_string
from django.test import SimpleTestCase

BASE = Path(__file__).resolve().parent.parent


class DialogLayerTests(SimpleTestCase):
    def test_backdrop_is_below_the_panel(self):
        css = (BASE / "static/css/fx-skin.css").read_text(encoding="utf-8")
        self.assertIn(".ui-modal > .ui-overlay { position: absolute; inset: 0; z-index: 0; }", css)
        self.assertIn(".ui-modal > .ui-modal-panel { position: relative; z-index: 1;", css)

    def test_global_dialogs_put_the_backdrop_at_z0(self):
        html = (BASE / "templates/components/overlays.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('class="ui-overlay" style="position:absolute;z-index:0"'), 2)   # confirm + shortcuts
        self.assertNotIn('class="ui-overlay" style="position:absolute" x-show', html)
        modal = (BASE / "templates/components/modal.html").read_text(encoding="utf-8")
        self.assertIn("position:absolute;z-index:0", modal)

    def test_form_cards_do_not_clip_popouts(self):
        css = (BASE / "static/css/fx-skin.css").read_text(encoding="utf-8")
        self.assertIn(".ui-form-section { overflow: visible; }", css)


class LookupWidgetTests(SimpleTestCase):
    def test_lookup_list_is_positioned_by_script(self):
        from catalog.models import Product
        from erp_ui.widgets import LookupSelect
        w = LookupSelect(Product, Product)
        with mock.patch("erp_ui.widgets.reverse", return_value="/x/lookup/"), mock.patch.object(LookupSelect, "_create_url", return_value=""), \
                mock.patch.object(LookupSelect, "_label", return_value=""):
            html = w.render("product", "", {"id": "id_product"})
        for needle in ('class="ui-lookup"', 'x-ref="inp"', ':style="menuStyle"', '@scroll.window.capture="open && place()"', 'ui-lookup-clear', "is-active"):
            self.assertIn(needle, html)
        self.assertNotIn('class="relative"', html)
