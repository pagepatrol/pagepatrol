from playwright.async_api import Page

from pagepatrol.checks import load_js
from pagepatrol.models import Finding, Rect, Severity

WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
MAX_NODES_PER_RULE = 25

# Errors are reserved for things that are actually broken. Accessibility
# violations are real problems, but a page with them still works.
_IMPACT = {
    "critical": Severity.WARNING,
    "serious": Severity.WARNING,
    "moderate": Severity.INFO,
    "minor": Severity.INFO,
}

# Rules that are really about whether people can read the page, not about
# assistive technology. Grouped separately so they stand out in the report.
_READABILITY_RULES = {"color-contrast", "color-contrast-enhanced", "link-in-text-block"}

_RUN_AXE = """
async ({ tags, maxNodes }) => {
  const result = await axe.run(document, {
    runOnly: { type: 'tag', values: tags },
    resultTypes: ['violations'],
    elementRef: true,
  });
  return result.violations.map((v) => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    helpUrl: v.helpUrl,
    total: v.nodes.length,
    nodes: v.nodes.slice(0, maxNodes).map((n) => ({
      // axe's own selectors are short and not always unique, so build ours.
      selector: n.element ? __pagepatrol.selectorFor(n.element) : n.target.flat().join(' '),
      message: (n.any[0] || n.all[0] || n.none[0] || {}).message || null,
      rect: n.element ? __pagepatrol.docRect(n.element) : null,
    })),
  }));
}
"""


async def run_axe(page: Page) -> list[Finding]:
    await page.evaluate(load_js("vendor/axe.min.js"))
    violations = await page.evaluate(_RUN_AXE, {"tags": WCAG_TAGS, "maxNodes": MAX_NODES_PER_RULE})

    findings = []
    for v in violations:
        category = "readability" if v["id"] in _READABILITY_RULES else "accessibility"
        severity = _IMPACT.get(v["impact"], Severity.WARNING)
        for node in v["nodes"]:
            findings.append(
                Finding(
                    check=f"axe/{v['id']}",
                    category=category,
                    severity=severity,
                    message=node["message"] or v["help"],
                    selector=node["selector"],
                    rect=node["rect"] and Rect(**node["rect"]),
                    details={
                        "rule": v["help"],
                        "help_url": v["helpUrl"],
                        "total_nodes": v["total"],
                    },
                )
            )
    return findings
