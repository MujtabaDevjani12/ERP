# UI / UX Audit

Scope: the original POS back office, the POS terminal, and the ERP screens (`erp_ui`). Findings come from reading the
templates and static assets; nothing here was measured in a browser, so performance numbers are estimates.

## 1. What existed before this work

| Area | State | Problem |
|---|---|---|
| Layout | `templates/base.html`: dark sidebar + white top bar, Tailwind (CDN) + Alpine | Sidebar was a separate hand-written file (`partials/sidebar_nav.html`, ~250 lines of repeated markup) and the ERP had a *second* sidebar (`erp_ui/nav.html`) → two menus, two looks, ERP reachable only through one link |
| Design tokens | Colours/radius/shadows hard-coded in 150 lines of `<style>` inside `base.html`, plus per-template Tailwind classes | No single source of truth; every page chose its own grays and radii |
| Dark mode | none | — |
| Tables | DataTables (jQuery + 8 scripts from CDN) loaded **on every page**, including pages without a table | ~600 KB of blocking scripts on the critical path of every screen (estimate) |
| Notifications | Flash messages rendered as banners that push the page down; notification inbox had no entry in the top bar | No unread indicator, no quick view |
| Search | none (only per-list filters) | No way to jump to a product, customer or invoice from anywhere |
| Confirmations | `window.confirm()` / `alert()` in `dashboard/pos.html` (≈ 6 places) and bare delete pages elsewhere | Browser-native dialogs break the visual language and cannot be styled or localised |
| Forms | Global rules for text inputs only; date-time, select-multiple and file inputs unstyled/inconsistent | Mixed heights, focus rings and error styles |
| Buttons | `.btn / .btn-primary / .btn-secondary` plus many ad-hoc `bg-brand-600 …` utility strings | Different padding/radius per page; no loading or disabled state |
| Feedback | No loading, skeleton or double-submit protection | Users can click Save twice |
| Responsive | Sidebar drawer existed; tables overflowed horizontally on phones | — |
| Accessibility | No skip link, no focus-visible styling beyond the form-field ring, icon buttons without labels | — |
| Sign-out | `<a href="/accounts/logout/">` | Django 5 `LogoutView` accepts POST only, so this link returns *405 Method Not Allowed* |
| POS terminal | Own `<head>`, own dark theme and CSS (`dashboard/pos.html`, 32 KB + 3 modals) | Visually separate from the rest of the product |
| ERP screens | Built on the same Tailwind classes as the old pages | Consistent with the old look only, no row-level preview, no saved filters |

## 2. Principles chosen

1. **Presentation only.** No view, URL name, model or service is changed to restyle a page.
2. **One system.** Tokens → components → shell → pages, in that order (`UI_DESIGN_SYSTEM.md`).
3. **Back-compatible.** Legacy class names (`.btn`, `.btn-primary`, `.datatable`, `.card-shadow`, Tailwind colour
   utilities) keep working, and are remapped for dark mode, so pages can be migrated one by one without breaking.
4. **Original design.** Calm neutral surfaces, one accent colour (the store's brand colour), restrained shadows, Inter.

## 3. Rollout

Status legend: **done** = delivered; **shell** = inherits the new shell/tokens/dark mode but its own markup is still the
old Tailwind layout; **todo** = not yet touched.

| Phase | Scope | Status |
|---|---|---|
| 1 | Audit + design-system documents | done |
| 2 | Design tokens, component styles, icon library, template tags, JS behaviours (toasts, confirm, drawer, palette) | done |
| 3 | App shell: unified collapsible role-aware sidebar, top bar, global search, notifications bell, dark mode, density | done |
| 4 | Shared partials (`generic_list`, `simple_form`, `confirm_delete`), login, password change, notifications inbox, dashboard home, ERP generic screens (list with column chooser / saved views / quick-view drawer, form with process stepper + activity timeline + quick-create drawer, module home, delete confirmation) | done (not yet browser-tested) |
| 5 | POS terminal restyle + shift screens | todo |
| 6 | Remaining legacy pages: catalog, inventory, purchasing, sales, customers, reports, settings, accounts | shell |
| 7 | Dedicated screens: production process tracker, QC, approvals inbox, payroll run | todo |

Every legacy page already renders inside the new shell (sidebar, top bar, search, dark-mode remap) after phase 3.

## 4. Known limits

* Tailwind is still loaded from the CDN (no build step). A compiled stylesheet would remove ~300 KB of runtime JIT
  and the "flash of unstyled content" risk; it needs Node on the developer machine.
* "Switch company / branch" is shown as information only: the data model ties a user to one store and has no switching
  mechanism, and adding one is a business-logic change.
* Dark mode for un-migrated pages is a colour remap, not a redesign; a few hard-coded inline colours may look off and
  should be fixed page by page.
* Browser matrix (Chrome / Edge / Firefox) and a formal contrast audit still have to be run on a real machine.
