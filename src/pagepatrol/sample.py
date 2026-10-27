import re
from typing import Any

# Plausible test data, picked by what a field looks like. Nothing here should
# be mistaken for real personal data.
BY_TYPE = {
    "email": "test@example.com",
    "password": "Test-Pass-1234",
    "tel": "+15555550123",
    "url": "https://example.com",
    "date": "2026-01-15",
    "datetime-local": "2026-01-15T10:30",
    "time": "10:30",
    "month": "2026-01",
    "week": "2026-W03",
    "color": "#3366cc",
    "search": "test",
}

BY_NAME = [
    (r"e-?mail", "test@example.com"),
    (r"pass(word)?|pwd", "Test-Pass-1234"),
    (r"phone|tel|mobile|gsm", "+15555550123"),
    (r"first.?name|given", "Test"),
    (r"last.?name|surname|family", "User"),
    (r"user.?name|login", "testuser"),
    (r"full.?name|^name$|your.?name|ad.?soyad", "Test User"),
    (r"company|organi[sz]ation|firma", "Example Inc"),
    (r"zip|postal|post.?code", "10001"),
    (r"city|town|şehir|\bil\b", "Springfield"),
    (r"country|ülke", "United States"),
    (r"address|street|adres", "1 Test Street"),
    (r"\bage\b|yaş", "30"),
    (r"url|website|site", "https://example.com"),
    (r"search|query|^q$|\bara\b", "test"),
    (r"subject|title|konu|başlık", "Test message"),
    (r"message|comment|note|body|description|mesaj|yorum|açıklama", "Hello, this is a test."),
]


def value_for(field: dict[str, Any]) -> str | None:
    """A value to type into a text-like field, or None to leave it alone."""
    kind = field.get("type", "")
    if kind in ("checkbox", "radio") or field.get("tag") == "select":
        return None
    if kind == "number" or kind == "range":
        low = _number(field.get("min"))
        high = _number(field.get("max"))
        value = low if low is not None else 1
        return str(int(value if high is None else min(value, high)))
    if kind in BY_TYPE:
        return BY_TYPE[kind]

    hints = " ".join(str(field.get(k) or "") for k in ("name", "autocomplete", "label")).lower()
    value = next((v for pattern, v in BY_NAME if re.search(pattern, hints)), None)
    if value is None:
        value = "Hello, this is a test." if field.get("tag") == "textarea" else "test"
    if field.get("maxlength"):
        value = value[: field["maxlength"]]
    return value


def option_for(field: dict[str, Any]) -> str | None:
    """The first real option of a dropdown, skipping "Choose one..." placeholders."""
    for option in field.get("options") or []:
        if option.get("value") and option.get("text"):
            return option["text"]
    return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
