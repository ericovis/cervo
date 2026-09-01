# cervo design system — "deploy receipt"

Terminal-receipt aesthetic for all cervo-served pages (default index, future error pages, listings). Monospace throughout, one amber accent, warm-dark base with a paper-light variant. Zero external requests: system fonts only, styles inline in each page.

## Principles
- Self-contained pages: the token block lives in `src/cervo/templates/_tokens.css`; `web/layout.py` inlines it into every page's `<style>` (and `web.default_page` carries it into deployed sites), so there is no shared stylesheet to serve or cache-bust.
- Dark is the base. Light comes from `@media (prefers-color-scheme: light)`; an explicit `[data-theme]` on `<html>` (set by the toggle, persisted in `localStorage["cervo-theme"]`) overrides both.
- One accent. Amber marks status, links, section labels, and prompt carets — nothing else. No additional hues.
- Voice: terse and lowercase for labels (`site`, `address`, `deployed`); uppercase letterspaced only for section headings; plain matter-of-fact sentences.

## Tokens
| Token | Dark | Light | Role |
|---|---|---|---|
| `--bg` | `#1b1a16` | `#f7f3ea` | page background |
| `--ink` | `#f0ead8` | `#2b2820` | headings, emphasized text |
| `--text` | `#cfc9ba` | `#4a463c` | body text, receipt values |
| `--muted` | `#8a8574` | `#857e6e` | secondary text, labels |
| `--accent` | `#e5a83c` | `#9a6a1f` | status, links, section labels |
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
- **Header** — flex, space-between: `cervo` wordmark (12px, 700, `--ink`) left; tagline (12px, `--muted`) + theme toggle right, 14px gap.
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
