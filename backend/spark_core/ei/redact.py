"""Keep student identifiers out of LLM prompts.

The generated parts of a prompt (performance summary, signals) never contain names or
IDs. Free-text notes can, so before anything is sent the student's full name, first
and last name, username/login and SIS ID are replaced with neutral words. Matching is
on word boundaries (see below for case rules). This is a guard against accidents, not a general
PII scrubber: it does not catch other people's names or nicknames.
"""
from __future__ import annotations

import re


def _variants(name: str | None) -> list[str]:
    if not name:
        return []
    name = name.strip()
    out = {name}
    if "," in name:                       # "Last, First"
        last, first = [p.strip() for p in name.split(",", 1)]
        out |= {f"{first} {last}", first, last}
    parts = [p for p in re.split(r"\s+", name.replace(",", " ")) if p]
    if len(parts) >= 2:
        out |= {parts[0], parts[-1], f"{parts[0]} {parts[-1]}"}
    return sorted((v for v in out if len(v) >= 2), key=len, reverse=True)


def redact(text: str, student_name: str | None, identifiers: list[str | None]) -> tuple[str, int]:
    """Return (redacted text, number of replacements)."""
    if not text:
        return text, 0
    # E-mail addresses first (they often contain the username), and they are never needed in a prompt.
    text, total = re.subn(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "[email]", text)
    for v in _variants(student_name):
        # Full names match in any case. A lone first or last name matches only when capitalised as written,
        # or in capitals, so a student called "Will" or "Grant" doesn't swallow "will" and "grant" in ordinary prose.
        if " " in v or "," in v:
            pats, flags = [v], re.IGNORECASE
        else:
            pats, flags = sorted({v, v[:1].upper() + v[1:].lower(), v.upper()}), 0
        for pat in pats:
            text, n = re.subn(rf"(?<![\w]){re.escape(pat)}(?![\w])", "the student", text, flags=flags)
            total += n
    for ident in identifiers:
        if ident and len(str(ident)) >= 3:
            text, n = re.subn(rf"(?<![\w]){re.escape(str(ident))}(?![\w])", "[student id]", text, flags=re.IGNORECASE)
            total += n
    return text, total
