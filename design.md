# NBTE ICT — Design System

This guide records the shared UI direction and the foundations now available.
The complete proposal and component examples are in
[master_startwork.md](./master_startwork.md), especially its design-system
section.

## Principles

- Design mobile layouts intentionally; do not only shrink desktop screens.
- Prefer clear hierarchy, useful whitespace, restrained NBTE green, and
  purposeful motion.
- Keep light/dark appearance driven by semantic CSS tokens.
- Use semantic controls, visible keyboard focus, 44px-or-larger mobile touch
  targets, and reduced-motion support.
- Keep authorization, filtering scope, and validation in Django.

## Tokens and compatibility

`static/css/tokens.css` is the existing token source. It is already used
throughout the application, so the v2 semantic names from the master plan
were added as aliases rather than replacing legacy names or changing the
existing spacing scale in one risky global sweep. New shared UI should use
semantic aliases; migrate older stylesheets incrementally and verify each
screen in both themes.

## Shared primitives

- `static/css/components.css` contains buttons and the reusable
  `nbte-page-header`, `nbte-empty-state`, `nbte-list-row`, and
  `nbte-confirm` styles.
- `templates/partials/page_header.html`,
  `templates/partials/empty_state.html`, and
  `templates/partials/list_row.html` provide escaped, reusable template
  structures.
- `static/js/dialog.js` exposes `NBTEConfirm.open(options)` and handles
  confirmation forms marked with `data-confirm`. It uses the same accessible
  DOM-based dialog on browsers with or without native `<dialog>` support.

The `nbte-` class prefix intentionally avoids silently restyling legacy
`.page-header` and `.empty-state` markup. Convert existing screens as part of
their own tested improvements rather than changing their appearance globally.

## Responsive and accessibility checks

Check representative screens at 320, 390, 768, 1024, and 1440 CSS pixels in
light and dark themes. Dialogs should remain viewport-constrained, use safe
area insets, place actions within thumb reach on phones, trap keyboard focus,
close with Escape/backdrop, and return focus to their trigger.
