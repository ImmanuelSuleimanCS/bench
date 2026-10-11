"""The checked area text check: does a task title, note, or project name look like it holds something
that stays off this Mac?

A warning for simple patterns only, not real protection: 9 or 10 digit numbers (with no other digit
touching them, so ID1234567890 counts), dashed numbers like 123-45-6789, email addresses, and the
hostname patterns from config.toml. The check itself never logs, saves, or sends what it found; it shows
it on screen and the person decides. Saving anyway stores the whole text in that area's database.
"""
import re

BUILT_IN = (
    ("9 or 10 digit number", re.compile(r"(?<!\d)\d{9,10}(?!\d)")),
    ("dashed number", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    ("email address", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
)


def findings(text: str, hostname_patterns: list[str]) -> list[str]:
    """Each match as 'kind: matched text', in the order the checks run, without repeats."""
    checks = [*BUILT_IN, *(("hostname", re.compile(p, re.IGNORECASE)) for p in hostname_patterns)]
    found: list[str] = []
    for kind, pattern in checks:
        for match in pattern.finditer(text):
            item = f"{kind}: {match.group(0)}"
            if item not in found:
                found.append(item)
    return found
