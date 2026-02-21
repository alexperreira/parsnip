# Accessibility Checklist (UI V1)

This checklist defines minimum accessibility requirements for all four UI screens:
case viewer, profile page, timeline, and evidence browser.

## 1) Keyboard Flow

- [ ] All interactive controls are reachable via keyboard only.
- [ ] Tab order follows visual reading order (left-to-right, top-to-bottom).
- [ ] No keyboard traps in drawers, side panels, or modals.
- [ ] Escape closes dismissible overlays and returns focus to trigger.
- [ ] Enter/Space activates buttons, toggles, and chips consistently.
- [ ] Arrow keys are supported where interaction model requires it (listbox, menu, tabs).
- [ ] Pagination controls are keyboard reachable and operable.

## 2) Focus Order and Visibility

- [ ] Every interactive element has a visible `:focus-visible` style.
- [ ] Focus ring uses design tokens and meets contrast on all backgrounds.
- [ ] Focus is moved intentionally after route changes to screen heading.
- [ ] Opening a detail drawer moves focus into drawer title/first control.
- [ ] Closing a drawer restores focus to previously focused trigger element.
- [ ] Skip link exists to jump to main content region.
- [ ] Focus order remains stable when filters update results dynamically.

## 3) Semantic Structure

- [ ] Exactly one `h1` per screen.
- [ ] Heading levels are nested logically with no level skips.
- [ ] Landmark roles are present: `header`, `nav`, `main`, and `aside` (where used).
- [ ] Data tables use semantic `table`, `thead`, `tbody`, and `th scope`.
- [ ] Timeline rows use list semantics or table semantics consistently.
- [ ] Icon-only actions include accessible names.

## 4) Screen Reader Labels and Announcements

- [ ] All form controls have explicit labels (`label` or `aria-label`).
- [ ] Filter chips announce selected/unselected state.
- [ ] Sort controls announce active sort key and direction.
- [ ] Pagination announces current page and total pages.
- [ ] Loading states announce progress with `aria-busy` or status regions.
- [ ] Empty states are announced as informative status messages.
- [ ] Error banners include concise redacted message and correlation ID text.
- [ ] Dynamic result updates announce count changes via polite live region.

## 5) Contrast and Visual Requirements

- [ ] Body text contrast is at least WCAG AA (4.5:1).
- [ ] Large text and UI components meet WCAG AA non-text contrast (3:1 where applicable).
- [ ] Focus indicators meet minimum contrast against adjacent colors.
- [ ] Status colors (success/warning/error) are not the only signal; include text/icon.
- [ ] Link styling remains distinguishable beyond color alone.

## 6) Form and Filter Accessibility

- [ ] Grouped filters use `fieldset` and `legend` where appropriate.
- [ ] Validation errors are programmatically associated to fields.
- [ ] Invalid filter params restored from URL present clear inline explanation.
- [ ] Reset/apply actions are keyboard reachable and screen-reader named.
- [ ] Range controls expose current min/max values to assistive tech.

## 7) Table and Timeline Specific Checks

- [ ] Column headers are announced with cell values in data tables.
- [ ] Sortable headers expose `aria-sort`.
- [ ] Row action controls have unique, contextual labels.
- [ ] Timeline unresolved section is announced as distinct group.
- [ ] Timeline event details are reachable without pointer interactions.

## 8) Motion and Reduced Motion

- [ ] Non-essential animations respect `prefers-reduced-motion: reduce`.
- [ ] Motion does not hide critical state changes.
- [ ] Transition timing does not block keyboard interaction.

## 9) Error and Recovery States

- [ ] Widget-level failures preserve access to unaffected widgets.
- [ ] Error states provide retry action with keyboard and screen-reader access.
- [ ] Redacted errors avoid exposing sensitive source content.
- [ ] 403 and 404 views include clear heading and recovery navigation.

## 10) Test Protocol

For each screen, run this pass before completion:

1. Keyboard-only traversal from top of page to footer.
2. Screen-reader smoke test (NVDA/VoiceOver) for headings, landmarks, tables, live updates.
3. Contrast check on core text, buttons, badges, and focus states.
4. Reduced-motion check with system preference enabled.

## 11) Release Gate

All items in sections 1-5 are required for UI v1 release.
Sections 6-10 are required for production readiness sign-off.
