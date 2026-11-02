#!/usr/bin/env python3
"""Read-only HTML index of daily ledger files. Never edits .jsonl lines."""

from __future__ import annotations

import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/var/lib/zta-desk"))
LEDGER = DATA_DIR / "ledger"
PUBLIC = DATA_DIR / "public"

DISCLAIMER = (
    "Map, not advice. ZeroTouchAlgo is not a SEBI-registered Investment Adviser "
    "or Research Analyst. Practice book only — no orders are placed. "
    "Investment in securities market are subject to market risks."
)

EXPLAIN = (
    "Each line includes the fingerprint of the line before it, so any later "
    "edit breaks the chain; the head hash is posted to Telegram at 15:35."
)


def rows() -> list[dict]:
    out = []
    for path in sorted(LEDGER.glob("*.jsonl"), reverse=True):
        events = [
            json.loads(ln)
            for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        head = events[-1]["hash"] if events else ""
        out.append(
            {
                "date": path.stem,
                "events": len(events),
                "head": head,
                "file": path.name,
            }
        )
    return out


def render(days: list[dict]) -> str:
    items = []
    for day in days:
        items.append(
            "<li>"
            f"<strong>{day['date']}</strong> — {day['events']} events"
            f" — head <code>{day['head']}</code> — "
            f"<a href=\"/ledger/{day['file']}\">download</a>"
            "</li>"
        )
    body = "\n".join(items) if items else "<li>No days recorded yet.</li>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZeroTouchAlgo desk ledger</title>
<style>
body {{ font-family: system-ui, sans-serif; background:#0b1f2a; color:#e8f4ef;
  max-width: 40rem; margin: 2rem auto; padding: 0 1rem; }}
a {{ color:#3ee6a0; }}
code {{ font-size: 0.85em; word-break: break-all; }}
p.note {{ color:#9bb; }}
</style>
</head>
<body>
<h1>Desk ledger</h1>
<p>{EXPLAIN}</p>
<ul>
{body}
</ul>
<p class="note">{DISCLAIMER}</p>
</body>
</html>
"""


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    html = render(rows() if LEDGER.exists() else [])
    dest = PUBLIC / "index.html"
    tmp = dest.with_suffix(".html.tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(dest)


if __name__ == "__main__":
    main()
