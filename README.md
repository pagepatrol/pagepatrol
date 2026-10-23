# PagePatrol

Open-source, local-first QA agent for web pages.

PagePatrol opens your page in a real browser at phone and desktop sizes and
looks for the problems people actually run into: text that overlaps or gets
cut off, text that is too small or too faint to read, layouts that scroll
sideways on a phone, broken images and links, JavaScript errors and failed
requests. Every finding comes with a screenshot that shows where it is.

Everything runs on your machine. No account, no API key needed for the checks
below. LLM-driven scenario testing with your own key is on the way.

> **Status:** early development. Expect rough edges and breaking changes.

## Quick start

```sh
uv tool install pagepatrol            # or: pipx install pagepatrol
pagepatrol install                    # one-time: download Chromium
pagepatrol check https://example.com
pagepatrol check ./site/index.html --open
pagepatrol check localhost:3000 -v mobile -v tablet -v 1440x900
```

`check` prints a summary and writes a report to `.pagepatrol/runs/<time>/`:

- `report.html` with marked-up screenshots per viewport
- `report.json` with every finding, for scripts and CI
- `screenshots/` with clean and annotated full-page captures

It exits with code 1 when it finds errors, so it can gate a CI job. Use
`--fail-on warning` to be stricter or `--fail-on never` to only report.

## What it checks

| Check | Level | |
|---|---|---|
| `overlap` | error / warning | Text on top of text, or text and controls covering each other |
| `horizontal-overflow` | error | Elements that make the page scroll sideways |
| `viewport-meta` | error | No `<meta name=viewport>`, so phones show the desktop layout zoomed out |
| `broken-image`, `broken-link` | error | Images that don't load, links that return 404/410/5xx |
| `js-exception`, `console-error` | error | Uncaught exceptions and console errors |
| `http-error`, `failed-request` | error | Resources that fail or return 4xx/5xx |
| `clipped-text` | warning / info | Text cut off by its container |
| `small-text` | warning | Text below 12px |
| `axe/*` | warning / info | WCAG 2.2 A/AA rules from [axe-core](https://github.com/dequelabs/axe-core), including color contrast |

Errors mean something is broken. Warnings are real quality problems on a page
that still works. Notes are worth a look but may be intentional.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run playwright install chromium
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

`tests/fixtures/` holds small pages that are broken on purpose, one per check.
If you add a check, add a fixture that proves it fires and, where it makes
sense, one that proves it stays quiet.

## License

[MIT](LICENSE). Bundles [axe-core](https://github.com/dequelabs/axe-core)
(MPL-2.0), see `src/pagepatrol/checks/vendor/axe-core.LICENSE`.
