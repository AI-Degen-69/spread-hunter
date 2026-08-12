# How to regenerate the dashboard's static Tailwind CSS

This regenerates the stylesheet the spread dashboard ships instead of the
`cdn.tailwindcss.com` runtime. You'll change a Tailwind class in the page
template, rebuild the stylesheet, and confirm the class renders styled.

## Why this exists

The dashboard used to load the ~350KB Tailwind Play CDN runtime — render-
blocking in `<head>`, fetched from a third-party CDN on every cold load, and a
hard offline dependency (the "Tailwind CDN / Google Fonts as offline
dependencies" audit item). The build replaces it with a minified stylesheet
covering exactly the classes `server/spread_dash_html.py` emits, shipped as an
inline constant in `server/_tailwind_css.py` and inlined into both pages. There
is **no runtime fallback anymore** — that is the point, and it is why the build
must be re-run whenever the class set changes.

## Prerequisites

- Node and npm on PATH (the build runs the Tailwind v4 CLI; a throwaway npm
  install per build — build-time dependency only, the generated CSS ships in
  the repo and the served pages need no Node).
- Network on the first build (npm cache). Repeat builds are fast.

## Steps

1. **Edit the classes** in `server/spread_dash_html.py` — a new `hover:...`,
   an arbitrary value like `shadow-[0_0_10px_rgba(...)]`, a new `md:` variant,
   anything. Both dashboard pages share `_HEAD`, so one edit covers both.

2. **Rebuild from the repo root:**

   ```bash
   python -m scripts.build_tailwind_css
   ```

   Expected output:

   ```text
   wrote server/_tailwind_css.py (29.4 KB minified)
   ```

3. **Restart the dashboard** so the server picks up the new constant:

   ```bash
   .venv/bin/uvicorn server.spread_dash:app --port 8800
   ```

## Verification

- The command printed `wrote server/_tailwind_css.py (... KB minified)`, and
  the file's header still says "Generated asset — do not edit by hand."
- Load the dashboard and check your changed element renders styled.
- Run the page tests — they pin both pages and include a regression guard that
  the Tailwind CDN must never return:

  ```bash
  python -m pytest tests/test_dashboard_page.py -q
  ```

## Troubleshooting

- **A new class renders unstyled.** The build wasn't re-run (or was re-run
  before the class was saved). There is no runtime fallback to catch this —
  re-run step 2 and hard-refresh.
- **`npm install failed`.** The build needs the npm registry for its
  throwaway install; check network and retry.
- **`tailwind build failed`.** The v4 `@source` directive couldn't read
  `server/spread_dash_html.py`; the error names the path — check the file
  exists and the command ran from the repo root.
- **`npx` / `npm` not found.** Install Node.js; the build fails fast with an
  explicit message rather than degrading silently.

The class-scanning gate is documented in `scripts/build_tailwind_css.py`'s
module docstring; the measurement that motivated the build (before/after
network, render-blocking scripts, font files) is in the research log, Session 52.
