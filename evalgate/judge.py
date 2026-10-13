"""LLM judge for the one Form 4 field code cannot check exactly: officer_title.

Code compares titles exactly (after normalising case and spacing), which is
right for most filings but cannot tell that "CEO" means "Chief Executive
Officer", and cannot check a title when the form only says "See Remarks"
and the real title is written in the Remarks section. The judge reads the
person's part of the page plus the Remarks and returns a binary verdict.

It is a measuring instrument, so it is validated before use
(scripts/judge_eval.py): true positive and true negative rates against
human-confirmed labels. Verdicts are cached (data/judge_cache/) so a report
can be re-derived without calling the model again.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

from core.config import settings

JUDGE_SYSTEM = """You check one field extracted from an SEC Form 4 filing: the reporting \
person's officer title.

You get the person's section of the form (box 5 "Relationship of Reporting Person(s) to \
Issuer", with [X] for checked boxes and the title written under it), the form's Remarks, \
and a CANDIDATE title.

The correct officer_title is the person's job title as an officer of the issuer, as the \
filing states it: the text written under box 5 when "Officer" is checked, or, when that \
text only says "See Remarks", the title given for this person in the Remarks. It is null \
when the person is not an officer of the issuer.

PASS if the candidate names the same title the filing states, allowing abbreviations, \
punctuation and word order (CEO = Chief Executive Officer; "EVP, General Counsel" = \
"Executive Vice President and General Counsel"). PASS a null candidate only when the \
person has no officer title.
FAIL if the candidate is a different title, adds or drops a role (CEO vs CEO and \
President), is not a job title at all (e.g. "Other", "10% Owner", "Member of group", a \
trust or fund name), is null for a person who has an officer title, or is the pointer \
"See Remarks" itself when the Remarks give the title.

Answer with JSON only: {"verdict": "PASS" or "FAIL", "reason": "<one short sentence>"}"""

_PERSON = re.compile(r"Name and Address of Reporting Person\*?[ \t]*\n([^\n]+)")
_CACHE = settings.data_dir / "judge_cache" / "officer_title.jsonl"
_lock = threading.Lock()


def _key(name: str) -> str:
    return " ".join(sorted(re.sub(r"[^\w\s]", " ", name.lower()).split()))


def owner_context(text: str, name: str) -> str:
    """The person's own box-5 block (the last one naming them: multi-owner
    pages repeat owner 1 in the header) and the form's Remarks."""
    blocks = list(_PERSON.finditer(text))
    mine = [m for m in blocks if _key(m.group(1)) == _key(name)] or blocks[:1]
    start = mine[-1].start() if mine else 0
    nxt = [m.start() for m in blocks if m.start() > start]
    end = min(nxt[0] if nxt else len(text), start + 1500)
    section = text[start:end]
    # Single-owner pages put the title after box 5 and before box 3; the last
    # owner block of a multi-owner page runs on into the footnotes and Remarks.
    for stop in ("3. Date of Earliest Transaction", "Explanation of Responses", "Remarks:"):
        section = section.split(stop)[0]
    i = text.find("Remarks:")
    remarks = text[i:i + 1500].split("** Signature")[0] if i >= 0 else "Remarks: (none)"
    return f"{section.strip()}\n\n{remarks.strip()}"


def judge_title(text: str, name: str, candidate: str | None) -> dict:
    """{"verdict": "PASS"|"FAIL", "reason": ...}, cached by input."""
    from serve.client import chat

    context = owner_context(text, name)
    user = (f"{context}\n\nReporting person: {name}\n"
            f"CANDIDATE officer_title: {json.dumps(candidate)}")
    key = hashlib.sha256((settings.large_model + JUDGE_SYSTEM + user).encode()).hexdigest()[:20]
    with _lock:
        if _CACHE.exists():
            for line in _CACHE.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                if rec["key"] == key:
                    return rec["result"]
    raw, _ = chat(settings.large_model, JUDGE_SYSTEM, user, max_tokens=200, json_mode=True)
    try:
        result = json.loads(raw)
        if result.get("verdict") not in ("PASS", "FAIL"):
            raise ValueError(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        # Never guess a verdict: an unreadable answer is reported, not scored.
        raise RuntimeError(f"judge answer unreadable: {raw[:200]!r}") from exc
    with _lock:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        with _CACHE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": key, "name": name, "candidate": candidate,
                                 "result": result}) + "\n")
    return result
