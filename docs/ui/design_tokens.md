# Design Tokens (V1)

These tokens define a deterministic baseline for the investigator UI.
Values are intentionally explicit and stable.

## Format and Naming

- Prefix: `--ui-`
- Token groups: `color`, `type`, `space`, `radius`, `elevation`, `focus`, `motion`, `layout`
- No screen-specific values in global token namespace.

## Color Tokens

### Core palette

```css
:root {
  --ui-color-bg-canvas: #f5f4ef;
  --ui-color-bg-surface: #ffffff;
  --ui-color-bg-subtle: #ece9df;

  --ui-color-text-primary: #1f2933;
  --ui-color-text-secondary: #52606d;
  --ui-color-text-muted: #7b8794;
  --ui-color-text-inverse: #ffffff;

  --ui-color-border-default: #d9d4c7;
  --ui-color-border-strong: #b8b09a;

  --ui-color-accent-primary: #0f766e;
  --ui-color-accent-primary-hover: #0b5f59;
  --ui-color-accent-secondary: #1d4e89;

  --ui-color-state-success: #2f855a;
  --ui-color-state-warning: #b7791f;
  --ui-color-state-danger: #c53030;
  --ui-color-state-info: #2b6cb0;

  --ui-color-confidence-high: #2f855a;
  --ui-color-confidence-medium: #b7791f;
  --ui-color-confidence-low: #c53030;

  --ui-color-provenance-chip-bg: #e6f0fb;
  --ui-color-provenance-chip-text: #1a365d;
}
```

### Usage rules

- Primary content uses `--ui-color-text-primary` on `--ui-color-bg-surface`.
- Confidence tags map by deterministic threshold policy (owned by screen contract).
- Error text and borders use `--ui-color-state-danger`.
- Do not use raw hex in component files.

## Typography Tokens

```css
:root {
  --ui-type-family-sans: "Public Sans", "Source Sans 3", "Segoe UI", sans-serif;
  --ui-type-family-mono: "IBM Plex Mono", "SFMono-Regular", monospace;

  --ui-type-size-100: 0.75rem;  /* 12 */
  --ui-type-size-200: 0.875rem; /* 14 */
  --ui-type-size-300: 1rem;     /* 16 */
  --ui-type-size-400: 1.125rem; /* 18 */
  --ui-type-size-500: 1.25rem;  /* 20 */
  --ui-type-size-600: 1.5rem;   /* 24 */

  --ui-type-line-tight: 1.2;
  --ui-type-line-normal: 1.45;
  --ui-type-line-relaxed: 1.65;

  --ui-type-weight-regular: 400;
  --ui-type-weight-medium: 500;
  --ui-type-weight-semibold: 600;
  --ui-type-weight-bold: 700;
}
```

### Type scale mapping

- App title: `size-500`, `weight-semibold`
- Screen title: `size-400`, `weight-semibold`
- Section header: `size-300`, `weight-semibold`
- Body text: `size-200`, `weight-regular`
- Dense metadata / table support text: `size-100`, `weight-regular`

## Spacing Tokens

```css
:root {
  --ui-space-0: 0;
  --ui-space-1: 0.25rem;  /* 4 */
  --ui-space-2: 0.5rem;   /* 8 */
  --ui-space-3: 0.75rem;  /* 12 */
  --ui-space-4: 1rem;     /* 16 */
  --ui-space-5: 1.25rem;  /* 20 */
  --ui-space-6: 1.5rem;   /* 24 */
  --ui-space-8: 2rem;     /* 32 */
  --ui-space-10: 2.5rem;  /* 40 */
  --ui-space-12: 3rem;    /* 48 */
}
```

### Spacing rules

- Grid gap baseline: `--ui-space-4`
- Card padding: `--ui-space-4`
- Dense rows: vertical `--ui-space-2`, horizontal `--ui-space-3`
- Section-to-section vertical rhythm: `--ui-space-6`

## Radius Tokens

```css
:root {
  --ui-radius-none: 0;
  --ui-radius-sm: 0.25rem;
  --ui-radius-md: 0.5rem;
  --ui-radius-lg: 0.75rem;
  --ui-radius-pill: 999px;
}
```

## Elevation Tokens

```css
:root {
  --ui-elevation-0: none;
  --ui-elevation-1: 0 1px 2px rgba(16, 24, 40, 0.08);
  --ui-elevation-2: 0 2px 8px rgba(16, 24, 40, 0.10);
  --ui-elevation-3: 0 6px 20px rgba(16, 24, 40, 0.14);
}
```

### Elevation rules

- Default cards: `elevation-1`
- Overlays/drawers: `elevation-2`
- Modal/critical overlays: `elevation-3`

## Focus State Tokens

```css
:root {
  --ui-focus-ring-color: #0f766e;
  --ui-focus-ring-width: 2px;
  --ui-focus-ring-offset: 2px;
  --ui-focus-outline: var(--ui-focus-ring-width) solid var(--ui-focus-ring-color);
}
```

### Focus behavior

- Use `:focus-visible` only.
- Never remove focus styles without tokenized replacement.
- Interactive controls must meet visible focus contrast on both canvas and surface backgrounds.

## Motion Tokens

```css
:root {
  --ui-motion-duration-fast: 120ms;
  --ui-motion-duration-base: 180ms;
  --ui-motion-duration-slow: 260ms;
  --ui-motion-ease-standard: cubic-bezier(0.2, 0, 0, 1);
}
```

### Motion rules

- Motion supports orientation only; never encodes business state.
- Disable non-essential transitions under `prefers-reduced-motion: reduce`.

## Layout Tokens

```css
:root {
  --ui-layout-max-width: 1440px;
  --ui-layout-content-width: 1200px;
  --ui-layout-sidebar-width: 280px;
  --ui-layout-header-height: 56px;
  --ui-layout-contextbar-height: 48px;
}
```

## Sample Token Export (JSON)

```json
{
  "color": {
    "bg": {"canvas": "#f5f4ef", "surface": "#ffffff", "subtle": "#ece9df"},
    "text": {"primary": "#1f2933", "secondary": "#52606d", "muted": "#7b8794"},
    "accent": {"primary": "#0f766e", "secondary": "#1d4e89"}
  },
  "spacing": [0, 4, 8, 12, 16, 20, 24, 32, 40, 48],
  "radius": {"sm": 4, "md": 8, "lg": 12, "pill": 999},
  "focus": {"ringColor": "#0f766e", "ringWidth": 2, "ringOffset": 2}
}
```
