"""
The admin builds its forms with admin-only widgets (popup 'raw id' lookups, split date/time, the filter-horizontal box)
that need Django-admin CSS/JS. normalize_form() swaps them for plain inputs / a type-to-search picker so the same forms
render properly inside the ERP screens.
"""
import json

from django import forms
from django.contrib.admin import widgets as admin_widgets
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html


class LookupSelect(forms.Widget):
    """Type-to-search picker for a foreign key (what the admin shows as a 'raw id' box with a popup)."""

    def __init__(self, model, for_model, attrs=None):
        super().__init__(attrs)
        self.model = model
        self.for_model = for_model

    def use_required_attribute(self, initial):
        return False

    def _label(self, value):
        if value in (None, ""):
            return ""
        try:
            obj = self.model._default_manager.filter(pk=value).first()
        except (ValueError, TypeError, ValidationError):
            return ""
        return str(obj) if obj is not None else ""

    def _create_url(self):
        """Quick-create panel URL, only for registers that exist as ERP screens."""
        from . import registry
        model, ma = registry.get_model_admin(self.model._meta.app_label, self.model._meta.model_name)
        return registry.add_url(model) if ma is not None else ""

    def render(self, name, value, attrs=None, renderer=None):
        attrs = attrs or {}
        field_id = attrs.get("id") or f"id_{name}"
        url = "{}?for={}".format(reverse("erp_ui:lookup", args=[self.model._meta.app_label, self.model._meta.model_name]),
                                 self.for_model._meta.label_lower)
        pk = "" if value in (None, "") else str(value)
        return format_html(
            '<div class="ui-lookup" x-data="erpLookup({}, {}, {}, {}, {})" @click.outside="open=false" '
            '@scroll.window.capture="open && place()" @resize.window="open && place()">'
            '<input type="hidden" name="{}" value="{}" :value="pk">'
            '<input type="text" id="{}" x-ref="inp" x-model="text" @input.debounce.250ms="typed()" @focus="search()" '
            '@keydown.arrow-down.prevent="move(1)" @keydown.arrow-up.prevent="move(-1)" @keydown.enter="enter($event)" @keydown.escape="open=false" '
            'autocomplete="off" role="combobox" aria-autocomplete="list" :aria-expanded="open" placeholder="Type to search&hellip;">'
            '<button type="button" x-show="pk" x-cloak @click="clear()" class="icon-btn ui-lookup-clear" aria-label="Clear">&#10005;</button>'
            '<ul x-show="open" x-cloak class="ui-menu" :style="menuStyle" role="listbox">'
            '<template x-for="(r, i) in results" :key="r.id"><li @click="pick(r)" @mousemove="idx = i" class="ui-menu-item" :class="{{ \'is-active\': i === idx }}" role="option" x-text="r.text"></li></template>'
            '<li x-show="!results.length" class="ui-help" style="padding:8px 10px;margin:0">No match</li>'
            '<li x-show="createUrl" x-cloak @click="create()" class="ui-menu-item" style="color:var(--ui-primary);font-weight:600;border-top:1px solid var(--ui-border);margin-top:4px" role="option">+ New record</li>'
            '</ul></div>',
            json.dumps(url), json.dumps(pk), json.dumps(self._label(value)), json.dumps(self._create_url()),
            json.dumps(str(self.model._meta.verbose_name)), name, pk, field_id)


def _plain_for(widget, field):
    """Return a replacement widget when `widget` is an admin-only one, else None."""
    if isinstance(widget, admin_widgets.AdminDateWidget):
        return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    if isinstance(widget, admin_widgets.AdminTimeWidget):
        return forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
    if isinstance(widget, admin_widgets.AdminURLFieldWidget):
        return forms.URLInput()
    if isinstance(widget, admin_widgets.AdminTextareaWidget):
        return forms.Textarea(attrs={"rows": 3})
    if isinstance(widget, (admin_widgets.FilteredSelectMultiple, admin_widgets.ManyToManyRawIdWidget)):
        return forms.SelectMultiple()
    return None


def normalize_form(form):
    model = getattr(getattr(form, "_meta", None), "model", None)
    for name, field in list(form.fields.items()):
        widget = field.widget
        while isinstance(widget, admin_widgets.RelatedFieldWidgetWrapper):
            widget = widget.widget
        field.widget = widget

        if isinstance(field, forms.SplitDateTimeField):
            replacement = forms.DateTimeField(
                required=field.required, label=field.label, help_text=field.help_text, initial=field.initial,
                disabled=field.disabled,
                widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"))
            form.fields[name] = replacement
            continue

        if isinstance(field, forms.ModelMultipleChoiceField):
            if isinstance(widget, (admin_widgets.ManyToManyRawIdWidget, admin_widgets.FilteredSelectMultiple,
                                   admin_widgets.AutocompleteSelectMultiple)):
                field.widget = forms.SelectMultiple(choices=field.choices)
                field.widget.is_required = field.required
            continue

        if isinstance(field, forms.ModelChoiceField) and isinstance(
                widget, (admin_widgets.ForeignKeyRawIdWidget, admin_widgets.AutocompleteSelect)):
            if model is not None:
                field.widget = LookupSelect(field.queryset.model, model)
            else:
                field.widget = forms.Select(choices=field.choices)
            field.widget.is_required = field.required
            continue

        replacement = _plain_for(widget, field)
        if replacement is not None:
            if isinstance(replacement, forms.SelectMultiple) and hasattr(field, "choices"):
                replacement.choices = field.choices
            replacement.is_required = field.required
            field.widget = replacement
    return form


def normalize_formset(formset):
    for form in formset.forms:
        normalize_form(form)
    normalize_form(formset.empty_form)
    return formset
