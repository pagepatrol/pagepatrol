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

### What it costs

You pay your provider directly; PagePatrol itself is free. Every `check`
prints how long it took, the tokens used and, when the model's price is
known, an estimate of the cost. The report shows the same.

Measured on real pages with the default `balanced` depth at phone and
desktop sizes:

| Page | Claude Sonnet 5.5 | Claude Opus 5.5 | Claude Haiku 5.5 |
|---|---|---|---|
| Small page (one screen) | ~$0.015 | ~$0.03 | <$0.001 |
| Long page (Hacker News, MDN) | ~$0.05 | ~$0.10 | ~$0.003 |

Opus and Haiku figures are scaled from the Sonnet runs by list price, so
treat them as rough. `--depth quick` looks at fewer screens and costs about
half; `thorough` looks at up to 12 screens per size and can cost three times
as much on long pages. Prices for Claude models ship with PagePatrol
(list prices as of 2026-10); for other providers `init` asks for them, and
local models through Ollama are free.

Screenshots of the page go to the provider you picked. Use `--no-llm` for
pages that shouldn't leave your machine, or a local model through Ollama.

## Your API key

PagePatrol has no server. Your key stays on your machine and is only ever
sent to the provider you chose, by that provider's official SDK.

It looks for a key in this order:

1. `PAGEPATROL_API_KEY`
2. the provider's usual variable: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
   `GEMINI_API_KEY` or `OPENROUTER_API_KEY`
3. a key you pasted into `pagepatrol init`, kept in your system keyring
   (Windows Credential Manager, macOS Keychain, Secret Service on Linux)

If `init` finds the key in an environment variable it doesn't store it. On
systems without a keyring, such as headless Linux, a pasted key goes to
`~/.pagepatrol/credentials.toml`, readable only by your user.
`pagepatrol config` shows which source is in use without printing the key.

The key is never written to reports, screenshots or the terminal; a test
checks this on every change. Pages under test run inside Chromium and can't
reach it, and the model never sees it either.

What this doesn't protect against: anything that already runs as your user
can read your keyring and environment, and so could a compromised dependency
of any tool you run. PagePatrol keeps its dependencies few and is published
from GitHub Actions through PyPI trusted publishing, with no long-lived token
that could be stolen. If you find a security problem, please report it
privately through GitHub's security advisories instead of a public issue.

## Scenarios

`check` looks at one page. A scenario walks through a flow the way a visitor
would, and runs the same checks after every step, so a problem shows up at
the step that caused it.

```yaml
# .pagepatrol/scenarios/contact.yaml
name: Contact form sends a message
url: /contact
steps:
  - fill: { target: Email, value: test@example.com }
  - fill: { target: Message, value: Hello }
  - click: Send
  - expect: Thanks for your message
```

```sh
pagepatrol run --base-url localhost:3000      # runs everything in .pagepatrol/scenarios
pagepatrol run checkout.yaml -b https://staging.example.com --open
```

Targets are what people see on the page: a button's text, a field's label or
placeholder, a link. PagePatrol finds them the way assistive technology does.
When that isn't enough, be explicit:
`{ role: button, name: Send }`, `{ label: Email }`, `{ css: "#send" }`,
`{ testid: send-button }`, adding `nth: 1` to pick one of several. Quote
values that contain `?`, `:` or `#`, as usual in YAML.

| Step | Example |
|---|---|
| `goto` | `goto: /pricing` |
| `click`, `hover`, `check`, `uncheck` | `click: Add to cart` |
| `fill`, `select` | `fill: { target: Email, value: a@b.co }` |
| `press` | `press: Enter` or `press: { key: Enter, target: Search }` |
| `wait` | `wait: 500` or `wait: { text: Loaded }` |
| `expect` | `expect: Saved`, `expect: { url: /thanks }`, `{ title: ... }`, `{ visible: ... }`, `{ hidden: ... }`, `{ no_text: Error }` |

### Plain-language steps

A step can also be a sentence. The AI model from `pagepatrol init` works out
the clicks and typing, one action at a time:

```yaml
name: Buy the cheapest mug
url: /shop
steps:
  - Add the cheapest mug to the cart
  - Go to checkout and fill in a test address
  - expect: Order summary
```

When it gets there, the concrete actions it took are saved to
`.pagepatrol/recordings.yaml`. Later runs replay those without calling the AI,
so they cost nothing and give the same result every time. If the page changes
and a recording stops working, the AI redoes the step and updates it. Use
`--fresh` to redo all of them, or `--no-llm` to only replay. The recordings
are plain YAML; you can copy them into the scenario if you'd rather keep
explicit steps.

A run with AI steps prints how many calls it made and what they cost. Each
action is one call with a screenshot, about half a cent with Claude Sonnet 5.5.

### Scenarios from your code

Don't want to write them yourself? Let the AI read the project:

```sh
pagepatrol plan ./my-app
```

It works with any language or framework. It reads the source files (respecting
`.gitignore`), tells you how many files it will send and roughly what that
costs, and asks before sending. Files like `.env`, keys and credentials are
never sent, and values that look like passwords or tokens are masked. It
then lists the scenarios it suggests; you pick which ones to save to
`.pagepatrol/scenarios/`. They use plain-language steps, so the first
`pagepatrol run` lets the AI do them and records the result.

On the demo app in this repo, planning cost about $0.01 and the first run of
the five planned scenarios about $0.08; runs after that replay for free.

A file can hold one scenario or a list under `scenarios:`, and each scenario
can set `viewport: mobile`. A failed step stops its scenario; the rest are
marked skipped. `run` exits with code 1 if any scenario failed.

On sites other than localhost and local files, PagePatrol asks before
clicking anything that looks permanent, such as Delete, Pay or Place order.
Pass `--yes` to skip the question. Without a terminal to ask in (CI), it
stops the scenario instead.

For pages behind a login, run `pagepatrol login https://example.com/login`
once. A browser opens, you log in, and the session is saved to
`.pagepatrol/auth.json`, which `run` then uses. That file holds your login
cookies, so keep `.pagepatrol/` out of version control.

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
