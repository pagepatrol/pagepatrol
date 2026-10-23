# PagePatrol

Open-source, local-first QA agent for web pages.

PagePatrol reads your project, works out how people are meant to use it, then
walks through those flows in a real browser. Along the way it collects
screenshots, console output and network errors, runs checks for things like
low-contrast text, overlapping elements and overflow, and asks the LLM of your
choice to review what it saw. You get a report with evidence for every finding.

It runs on your machine with your own API key. Any provider works, including
local models.

> **Status:** early development. Nothing useful to install yet.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pagepatrol --help
uv run pytest
uv run ruff check .
```

## License

[MIT](LICENSE)
