#!/usr/bin/env python3
"""Host checks after Lab overlay. No secrets. No OA imports."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

OA = Path("/opt/openalgo/current")


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return ((p.stdout or "") + (p.stderr or "")).strip()


def main() -> None:
    print("coa_mod", (OA / "services" / "greeks_lab_coa.py").is_file())
    chunks = sorted((OA / "frontend" / "dist" / "assets").glob("GreeksLab-*.js"))
    print("chunks", [p.name for p in chunks])
    text = (chunks[0].read_text(encoding="utf-8", errors="ignore") if chunks else "")
    print("has_getCoaBoards", "greekslab/api/coa" in text)
    print("has_InsightCard", "Decay" in text or "desk read" in text.lower() or "quadshield" in text.lower())
    briefs = sorted(Path("/var/lib/strike-desk").glob("public-brief*.json"))
    print("briefs", [p.name for p in briefs])
    for path in briefs:
        raw = json.loads(path.read_text(encoding="utf-8"))
        print(
            "brief",
            path.name,
            "label",
            raw.get("scenario_label"),
            "last",
            raw.get("last") or raw.get("spot"),
            "mode",
            oct(path.stat().st_mode)[-3:],
        )
    print("root_headers")
    print(
        run(
            [
                "curl",
                "-skI",
                "--resolve",
                "lab.zerotouchalgo.com:443:127.0.0.1",
                "https://lab.zerotouchalgo.com/",
            ]
        ).splitlines()[:12]
    )
    print(
        "coa_http",
        run(
            [
                "curl",
                "-sk",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--resolve",
                "lab.zerotouchalgo.com:443:127.0.0.1",
                "https://lab.zerotouchalgo.com/greekslab/api/coa",
            ]
        ),
    )
    print("openalgo", run(["systemctl", "is-active", "openalgo.service"]))
    print("coa_unit", run(["systemctl", "is-active", "strike-desk-coa.service"]))
    print("desk", run(["systemctl", "is-active", "strike-desk.service"]))
    print("VERIFY_OK")


if __name__ == "__main__":
    main()
