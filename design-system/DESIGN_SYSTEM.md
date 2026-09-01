# cervo design system — "deploy receipt"

Terminal-receipt aesthetic for all cervo-served pages (default index, future error pages, listings). Monospace throughout, one amber accent, warm-dark base with a paper-light variant. Pages render from their own bytes: system fonts only, styles inline, drawings inline — the brand's icon files are the one thing a page points out to, because browsers and social scrapers fetch those outside the render.

## Principles
- Self-contained pages: the token block lives in `src/cervo/templates/_tokens.css`; `web/layout.py` inlines it into every page's `<style>` (and `web.default_page` carries it into deployed sites), so there is no shared stylesheet to serve or cache-bust.
- Dark is the base. Light comes from `@media (prefers-color-scheme: light)`; an explicit `[data-theme]` on `<html>` (set by the toggle, persisted in `localStorage["cervo-theme"]`) overrides both.
- One accent. Amber marks status, links, section labels, and prompt carets — nothing else. No additional hues.
- One mark. The antler (cervo = deer) appears amber beside the wordmark and nowhere else on a page; it is inlined in `currentColor`, never fetched.
- Voice: terse and lowercase for labels (`site`, `address`, `deployed`); uppercase letterspaced only for section headings; plain matter-of-fact sentences.

## Tokens
| Token | Dark | Light | Role |
|---|---|---|---|
| `--bg` | `#1b1a16` | `#f7f3ea` | page background |
| `--ink` | `#f0ead8` | `#2b2820` | headings, emphasized text |
| `--text` | `#cfc9ba` | `#4a463c` | body text, receipt values |
| `--muted` | `#8a8574` | `#6e6858` | secondary text, labels |
| `--accent` | `#e5a83c` | `#8a5e19` | status, links, section labels |
| `--rule` | `#34322b` | `#e2dac8` | section borders, chip borders |
| `--dotted` | `#4a463c` | `#b8b09c` | receipt dotted leaders |
| `--code-bg` | `#24221c` | `#efe8d6` | endpoint/code chips |

## Typography
Stack: `ui-monospace, "SF Mono", Menlo, Consolas, monospace` — everything.
- Body: 13px / 1.65, `--text`
- Page title (h1): 26px / 600, `--ink` (22px under 480px)
- Section label (h2): 11px, letter-spacing 0.18em, UPPERCASE, `--accent`, weight 400
- Meta (wordmark, tagline, status): 12px; status letter-spacing 0.12em
- Fine print / footer sections: 12px `--muted`
- Toggle: 11px

## Layout & spacing
- Content column: max-width 640px, centered, padding 44px 24px 56px
- Hero: status 40px below header; h1 margins 10px / 14px
- Sections: margin-top 42px, `1px solid --rule` top border, padding-top 24px, first paragraph +10px
- Receipt block: margin-top 24px, full content column, 10px row gap
- One measure: prose, receipts, and figures all run the full 640px column

## Components
- **Header** — flex, space-between: the lockup left; tagline (12px, `--muted`) + theme toggle right, 14px gap.
- **Lockup** — link, inline-flex, 7px gap: the antler mark (15px square, `--accent`) then `cervo` (12px, 700, `--ink`), undecorated. Hover takes both to `--ink`. The mark is `aria-hidden` — the wordmark next to it already says the name.
- **Theme toggle** — transparent button, `1px solid --rule`, radius 4px, padding 3px 10px, `--muted`; hover: text and border `--accent`. Label "☀ light" in dark mode, "☾ dark" in light.
- **Status line** — `● LIVE` in `--accent`.
- **Receipt row** — flex baseline, 10px gap: muted key, `flex:1` dotted-leader span (`1px dotted --dotted`), value in `--text` (links in `--accent`).
- **Endpoint chip** — inline-block, `--code-bg`, `1px solid --rule`, radius 6px, padding 10px 16px; link undecorated `--accent`.
- **Prompt list** — 8px row gap; `>` caret in `--accent`, prompt text in `--ink`.
- **Links** — `--accent`, underline, `text-underline-offset: 3px`; hover `--ink`.

## Theme mechanics (reuse verbatim)
1. Tokens on `:root` (dark) + light overrides in the media query + both palettes duplicated under `[data-theme="dark"|"light"]`; set `color-scheme` alongside each.
2. Head script applies `localStorage["cervo-theme"]` to `document.documentElement.dataset.theme` before paint.
3. Toggle computes the effective theme (`dataset.theme` else media query), flips it, persists it, relabels itself.

## Brand assets
The set lives in `src/cervo/brand/`, `web/brand.py` is the only thing that reads it, and `web/routes.py` serves the files browsers and scrapers ask for. The mark is antlers over a stem, drawn on a 24-unit grid with round caps and 1.9 stroke — amber `#e5a83c` on the dark tile `#1b1a16`, straight from the tokens above.

| File | Served at | Used for |
|---|---|---|
| `mark.svg` | — | inlined in the header lockup; no size of its own (CSS sizes it), `currentColor`, `aria-hidden` |
| `favicon.svg` | `/favicon.svg` | the tab icon, full mark on the rounded dark tile |
| `favicon-32.png` | `/favicon-32.png` | the 32px tab icon |
| `favicon-16.png` | `/favicon-16.png` | the 16px tab icon: the simplified cut |
| `apple-touch-icon-180.png` | `/apple-touch-icon-180.png` | the iOS home-screen icon |
| `og-image-1200x630.png` | `/og-image-1200x630.png` | the link preview card (`summary_large_image`, LinkedIn's 1.91:1 slot) |
| `favicon-small.svg` | — | the vector of the simplified cut, source for the 16px raster |
| `favicon-48.png`, `icon-512.png`, `mark-512-transparent.png` | — | the rest of the delivered set: larger tabs, PWA/large use, and the mark alone with no plate (what the README uses) |

- **The simplified cut.** At 16px the two lower tines silt up, so that size drops them and keeps the stem and main beams — same silhouette, still legible.
- **Filenames are the cache-busting handle.** Every file is served under its delivered name, sizes included, because a scraper re-reads a card only on its own schedule (LinkedIn only through its Post Inspector). Revising the card means a new filename, which means a new URL.
- **Head tags.** Every page's `<head>` carries the icons, a description, the Open Graph card with its declared 1200×630, two `theme-color` metas matching each theme's `--bg` (so a mobile browser's chrome continues the page), and `twitter:card: summary_large_image`. The one tag from the brand sheet cervo does not emit is `og:url` — the layout renders a page without knowing the path it was requested at, and a wrong canonical URL is worse than none.
- **Base-relative.** Icon and card URLs are prefixed with the page's `base`: empty on cervo's own pages, the apex origin on a site's default page, where a relative icon would be looked for on the subdomain and found nowhere. The card's URL is absolute either way — a scraper has nothing to resolve a relative one against.
- Files are served with `cache-control: public, max-age=86400`. They change only when the brand does.
