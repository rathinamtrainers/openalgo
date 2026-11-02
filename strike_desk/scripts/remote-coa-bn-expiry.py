"""Print BANKNIFTY/NIFTY/SENSEX OpenAlgo expiries. Never prints the API key."""

from __future__ import annotations

import json
import os
from pathlib import Path

import urllib.request

ENV_CANDIDATES = [
    Path("/run/strike-desk/env"),
    Path("/etc/strike-desk/strike-desk.env"),
]


def _load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            out.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    return out


def _post(base: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    env = _load_env()
    key = env.get("STRIKE_DESK_OPENALGO_API_KEY") or env.get("OPENALGO_API_KEY") or ""
    base = env.get("STRIKE_DESK_OPENALGO_BASE_URL") or "http://127.0.0.1:5000"
    if not key:
        raise SystemExit("no openalgo api key in env files")
    for underlying, exchange in (
        ("BANKNIFTY", "NFO"),
        ("NIFTY", "NFO"),
        ("SENSEX", "BFO"),
    ):
        try:
            data = _post(
                base,
                "/api/v1/expiry",
                {
                    "apikey": key,
                    "symbol": underlying,
                    "exchange": exchange,
                    "instrumenttype": "options",
                },
            )
        except Exception as exc:
            print(underlying, "ERROR", type(exc).__name__, str(exc)[:180])
            continue
        dates = data.get("data") or data.get("expiry") or []
        print(underlying, data.get("status"), data.get("message"), "n=", len(dates) if isinstance(dates, list) else "?")
        if isinstance(dates, list):
            print(" ", ", ".join(str(x) for x in dates[:24]))


if __name__ == "__main__":
    main()
