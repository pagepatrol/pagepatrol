# PagePatrol

Open-source, local-first QA agent for web pages.

PagePatrol opens your page in a real browser at phone and desktop sizes and
looks for the problems people actually run into: text that overlaps or gets
cut off, text that is too small or too faint to read, layouts that scroll
sideways on a phone, broken images and links, JavaScript errors and failed
requests. Every finding comes with a screenshot that shows where it is.

Everything runs on your machine. The checks need no account and no API key.
If you want, connect the AI model of your choice and it will look over the
screenshots too, the way a human tester would.

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

## AI review (optional)

```sh
pagepatrol init        # pick a provider and model, paste your key
pagepatrol check https://example.com --depth thorough
```

With a model set up, every `check` also sends the screenshots and the list of
findings to it. The model:

- confirms each finding, or marks it as intentional or a false alarm (those
  drop to notes, with the reason shown in the report)
- points out visible problems the checks can't measure: text over busy
  images, broken alignment, placeholder text, error messages on the page

Supported: Anthropic, OpenAI, Google Gemini, OpenRouter, Ollama and any
OpenAI-compatible server (LM Studio, vLLM and others). The model needs to
accept images.

| `--depth` | Screens per viewport | Effort |
|---|---|---|
| `quick` | 2 | low |
| `balanced` (default) | 4 | medium |
| `thorough` | 12 | high |

Keys are read from `PAGEPATROL_API_KEY` or the provider's usual variable
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
`OPENROUTER_API_KEY`), or from `~/.pagepatrol/credentials.toml` if you paste
one into `init`. `pagepatrol config` shows what is in use.

Screenshots of the page go to the provider you picked. Use `--no-llm` for
pages that shouldn't leave your machine, or a local model through Ollama.

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
