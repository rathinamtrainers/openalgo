#!/usr/bin/env python3
"""Install CoA trade blotter. Restarts strike-desk-coa and openalgo.

Does not restart strike-desk.service.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

SRC = Path("/tmp/sd-coa-trades")
COA = Path("/opt/strike-desk/current/strike_desk/src/strike_desk")
OA = Path("/opt/openalgo/current")
STATE = Path("/var/lib/strike-desk")
LOG = STATE / "coa-trades.jsonl"
TGZ = Path("/tmp/sd-lab-btc-dist.tgz")
MAP = Path("/tmp/sd-lab-map")
COA_FILES = ("ltp_coa_trades.py", "ltp_coa_live.py")
OA_FILES = (
    ("greeks_lab_coa.py", OA / "services" / "greeks_lab_coa.py"),
    ("greeks_lab.py", OA / "blueprints" / "greeks_lab.py"),
)

SEED = [
    {
        "id": "sensex:2026-10-30T09:35:05+05:30",
        "board": "sensex",
        "title": "Sensex",
        "side": "PE",
        "scenario": "1.6",
        "in_at": "2026-10-30T09:35:05+05:30",
        "in_ist": "09:35",
        "out_at": "2026-10-30T09:39:12+05:30",
        "out_ist": "09:39",
        "day": "30 Oct 2026",
        "in_label": "PE on CoA 1.6",
        "out_label": "scenario kill 1.9",
        "rule": "scenario kill CoA 1.9",
        "hold_s": 247,
        "hold_label": "~4 min",
        "ist_span": "09:35–09:39",
        "entry_ltp": 607.1,
        "exit_ltp": 628.1,
        "strike": 74300.0,
        "strike_label": "74300 PE · 05 Nov 2026",
        "expiry": "05 Nov 2026",
        "symbol": "SENSEX05NOV2674300PE",
        "lot_size": 20,
        "lots": 1,
        "qty": 20,
        "currency": "INR",
        "peak_ltp": None,
        "peak_pct": None,
        "mark_ltp": 628.1,
        "atm_pnl": 420.0,
        "mfe": None,
        "open": False,
        "exit_scenario": "1.9",
    },
    {
        "id": "banknifty:2026-10-30T11:56:22+05:30",
        "board": "banknifty",
        "title": "Banknifty",
        "side": "CE",
        "scenario": "1.1",
        "in_at": "2026-10-30T11:56:22+05:30",
        "in_ist": "11:56",
        "out_at": "2026-10-30T11:59:30+05:30",
        "out_ist": "11:59",
        "day": "30 Oct 2026",
        "in_label": "CE on CoA 1.1",
        "out_label": "spot target opposite S/R",
        "rule": "spot target opposite S/R",
        "hold_s": 188,
        "hold_label": "~3 min",
        "ist_span": "11:56–11:59",
        "entry_ltp": 877.5,
        "exit_ltp": 932.9,
        "strike": 55000.0,
        "strike_label": "55000 CE · 23 Nov 2026",
        "expiry": "23 Nov 2026",
        "symbol": "BANKNIFTY23NOV2655000CE",
        "lot_size": 30,
        "lots": 1,
        "qty": 30,
        "currency": "INR",
        "peak_ltp": None,
        "peak_pct": None,
        "mark_ltp": 932.9,
        "atm_pnl": 1662.0,
        "mfe": None,
        "open": False,
        "exit_scenario": "1.1",
    },
    {
        "id": "banknifty:2026-10-30T12:26:35+05:30",
        "board": "banknifty",
        "title": "Banknifty",
        "side": "CE",
        "scenario": "1.1",
        "in_at": "2026-10-30T12:26:35+05:30",
        "in_ist": "12:26",
        "out_at": "2026-10-30T12:29:43+05:30",
        "out_ist": "12:29",
        "day": "30 Oct 2026",
        "in_label": "CE on CoA 1.1",
        "out_label": "scenario kill 1.5",
        "rule": "scenario kill CoA 1.5",
        "hold_s": 188,
        "hold_label": "~3 min",
        "ist_span": "12:26–12:29",
        "entry_ltp": 884.7,
        "exit_ltp": 879.6,
        "strike": 55000.0,
        "strike_label": "55000 CE · 23 Nov 2026",
        "expiry": "23 Nov 2026",
        "symbol": "BANKNIFTY23NOV2655000CE",
        "lot_size": 30,
        "lots": 1,
        "qty": 30,
        "currency": "INR",
        "peak_ltp": None,
        "peak_pct": None,
        "mark_ltp": 879.6,
        "atm_pnl": -153.0,
        "mfe": None,
        "open": False,
        "exit_scenario": "1.5",
    },
    {
        "id": "banknifty:2026-10-30T12:46:21+05:30",
        "board": "banknifty",
        "title": "Banknifty",
        "side": "CE",
        "scenario": "1.1",
        "in_at": "2026-10-30T12:46:21+05:30",
        "in_ist": "12:46",
        "out_at": "2026-10-30T12:48:27+05:30",
        "out_ist": "12:48",
        "day": "30 Oct 2026",
        "in_label": "CE on CoA 1.1",
        "out_label": "scenario kill 1.5",
        "rule": "scenario kill CoA 1.5",
        "hold_s": 126,
        "hold_label": "~2 min",
        "ist_span": "12:46–12:48",
        "entry_ltp": 872.95,
        "exit_ltp": 855.35,
        "strike": 55000.0,
        "strike_label": "55000 CE · 23 Nov 2026",
        "expiry": "23 Nov 2026",
        "symbol": "BANKNIFTY23NOV2655000CE",
        "lot_size": 30,
        "lots": 1,
        "qty": 30,
        "currency": "INR",
        "peak_ltp": None,
        "peak_pct": None,
        "mark_ltp": 855.35,
        "atm_pnl": -528.0,
        "mfe": None,
        "open": False,
        "exit_scenario": "1.5",
    },
    {
        "id": "banknifty:2026-10-30T12:58:51+05:30",
        "board": "banknifty",
        "title": "Banknifty",
        "side": "CE",
        "scenario": "1.1",
        "in_at": "2026-10-30T12:58:51+05:30",
        "in_ist": "12:58",
        "out_at": "2026-10-30T13:01:58+05:30",
        "out_ist": "13:01",
        "day": "30 Oct 2026",
        "in_label": "CE on CoA 1.1",
        "out_label": "scenario kill 1.5",
        "rule": "scenario kill CoA 1.5",
        "hold_s": 187,
        "hold_label": "~3 min",
        "ist_span": "12:58–13:01",
        "entry_ltp": 865.35,
        "exit_ltp": 859.9,
        "strike": 55000.0,
        "strike_label": "55000 CE · 23 Nov 2026",
        "expiry": "23 Nov 2026",
        "symbol": "BANKNIFTY23NOV2655000CE",
        "lot_size": 30,
        "lots": 1,
        "qty": 30,
        "currency": "INR",
        "peak_ltp": None,
        "peak_pct": None,
        "mark_ltp": 859.9,
        "atm_pnl": -163.5,
        "mfe": None,
        "open": False,
        "exit_scenario": "1.5",
    },
]


def _unix(src: Path, dest: Path) -> None:
    dest.write_bytes(src.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    dest.chmod(0o644)
    print("wrote", dest)


def _seed(*, replace: bool = False) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    have: set[str] = set()
    if LOG.is_file() and not replace:
        for line in LOG.read_text(encoding="utf-8").splitlines():
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict) and raw.get("id"):
                have.add(str(raw["id"]))
    added = 0
    with LOG.open("a", encoding="utf-8") as handle:
        for row in SEED:
            if row["id"] in have:
                continue
            handle.write(json.dumps(row) + "\n")
            added += 1
    LOG.chmod(0o644)
    subprocess.run(["chown", "strikedesk:strikedesk", str(LOG)], check=True)
    print("seeded", added)


def _dist() -> None:
    if MAP.exists():
        shutil.rmtree(MAP)
    MAP.mkdir(parents=True)
    with tarfile.open(TGZ, "r:gz") as archive:
        archive.extractall(MAP)
    src_dist = MAP / "dist"
    if not (src_dist / "index.html").is_file():
        raise SystemExit("missing dist/index.html")
    blob = "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in src_dist.glob("assets/GreeksLab-*.js")
    )
    if "CoA trades" not in blob or "ATM-leg" not in blob:
        raise SystemExit("built dist is missing CoA trade log")
    dest = OA / "frontend" / "dist"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src_dist, dest)
    subprocess.run(["chown", "-R", "openalgo:openalgo", str(dest)], check=True)


def main() -> None:
    enrich = "--enrich" in sys.argv
    if not COA.is_dir():
        raise SystemExit(f"missing {COA}")
    for name in COA_FILES:
        src = SRC / name
        if not src.is_file():
            raise SystemExit(f"missing {src}")
        dest = COA / name
        _unix(src, dest)
        subprocess.run(["chown", "strikedesk:strikedesk", str(dest)], check=True)
    live = (COA / "ltp_coa_live.py").read_text(encoding="utf-8")
    if "record_exit" not in live or "_blotter_contract" not in live:
        raise SystemExit("ltp_coa_live missing trade log hooks")
    for name, dest in OA_FILES:
        src = SRC / name
        if not src.is_file():
            raise SystemExit(f"missing {src}")
        _unix(src, dest)
        subprocess.run(["chown", "openalgo:openalgo", str(dest)], check=True)
    oa_coa = (OA / "services" / "greeks_lab_coa.py").read_text(encoding="utf-8")
    if "def get_coa_trades" not in oa_coa or "atm_pnl" not in oa_coa:
        raise SystemExit("greeks_lab_coa missing get_coa_trades")
    _dist()
    _seed(replace=enrich)
    subprocess.run(["systemctl", "restart", "strike-desk-coa.service"], check=True)
    subprocess.run(["systemctl", "restart", "openalgo.service"], check=True)
    subprocess.run(["systemctl", "is-active", "strike-desk-coa.service"], check=True)
    subprocess.run(["systemctl", "is-active", "openalgo.service"], check=True)
    subprocess.run(["systemctl", "is-active", "strike-desk.service"], check=True)
    print("COA_TRADES_OK")


if __name__ == "__main__":
    main()
