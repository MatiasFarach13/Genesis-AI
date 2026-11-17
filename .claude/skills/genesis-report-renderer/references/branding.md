# Branding: intive | GENESIS

Only the brand colors and the semantic status colors are fixed. Everything is defined as CSS variables in `assets/styles.css`; components must use the variables, never hex values.

The report uses a **dark theme** on screen (`color-scheme: dark`) and switches to a light palette automatically when printed (`@media print`) to save ink.

## Brand

| Token | Value | Use |
|---|---|---|
| `--brand-dark` | `#f8fafc` | "intive" wordmark (light on dark) |
| `--brand-genesis` | `#2dd4bf` | "GENESIS" wordmark (teal → cyan → indigo gradient), accents, progress, focus ring |
| `--brand-accent` | `#818cf8` | Secondary accent (gradients) |
| `--bg` | `#070b14` | Page background (with soft teal/indigo glows) |
| `--card` / `--card-2` | `#0f1626` / `#131c2f` | Cards (border `#1e2a42`, radius 14px, deep shadow) |
| `--text` / `--muted` / `--faint` | `#e2e8f0` / `#94a3b8` / `#64748b` | Text hierarchy |

Wordmark: `intive` (weight 700, `--brand-dark`) · `|` (dim gray, weight 300) · `GENESIS` (weight 700, gradient).

## Status colors

Tuned for contrast on dark backgrounds; tints use `color-mix(... transparent)`, never white.

| Status | Color | Notes |
|---|---|---|
| `pass` | `#34d399` | |
| `fail` | `#f87171` | |
| `manual` | `#fbbf24` | |
| `info` | `#60a5fa` | |
| `gap` | `#fb923c` | Provisional (pending design decision) |
| `contradiction` | `#fb7185` | Provisional (pending design decision) |
| `discrepancy` | `#a78bfa` | Provisional (pending design decision) |
| `na` | `#94a3b8` | |

## Typography

System font stack with Inter first: `Inter, "Segoe UI", system-ui, -apple-system, Roboto, sans-serif`. Inter is used only if installed locally; no web fonts are downloaded.

## Rules

- No CDN, no remote fonts, no remote icons. Icons are inline SVG in `renderer.js`.
- Statuses are always shown with color **and** text/icon (never color only).
- Print (`@media print`) expands all collapsibles, hides controls and keeps colors.
- Tone: short, imperative, no prose. Spanish by default (`meta.language`).
