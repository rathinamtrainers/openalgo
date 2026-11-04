#!/usr/bin/env python3
"""Deploy guard: Analyzer plan trio must type-check before restart.

Refuses a slim journal (no PositionRow). Does not overlay files. Does not
restart. No Hands, no TT.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_PKG = Path("/opt/strike-desk/current/strike_desk/src/strike_desk")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=IST).isoformat()


def _fn_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = list(node.args.args) + list(node.args.kwonlyargs)
    return [arg.arg for arg in args]


def _has_optional_arg(node: ast.FunctionDef, name: str) -> bool:
    names = _fn_args(node)
    if name not in names:
        return bool(node.args.vararg)
    index = names.index(name)
    defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + list(node.args.defaults)
    if index < len(node.args.args):
        return defaults[index] is not None or node.args.vararg is not None
    return True


def _functions(tree: ast.AST) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }


def check_journal(source: str) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(source)
    classes = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    if "PositionRow" not in classes:
        errors.append("journal.py is slim — missing class PositionRow; refuse overlay")
    fns = _functions(tree)
    live = fns.get("live_symbols")
    if live is None:
        errors.append("journal.py missing live_symbols")
    elif "trading_day" not in _fn_args(live) and live.args.vararg is None:
        errors.append("journal.live_symbols does not accept trading_day")
    elif live.args.vararg is None and not _has_optional_arg(live, "trading_day"):
        # required trading_day is OK for the host PositionRow journal
        pass
    for name in ("session_stopped", "open_approvals"):
        if name not in fns:
            errors.append(f"journal.py missing {name}")
    return errors


def check_client(source: str) -> list[str]:
    fns = _functions(ast.parse(source))
    if "broker_token_error" not in fns:
        return ["openalgo_client.py missing broker_token_error"]
    return []


def _has_getattr(source: str, owner: str, name: str) -> bool:
    return f'getattr({owner}, "{name}"' in source or f"getattr({owner}, '{name}'" in source


def check_graph(source: str) -> list[str]:
    errors: list[str] = []
    if "live_symbols" not in source:
        errors.append("graph.py does not mention live_symbols")
    if "broker_token_error" not in source:
        errors.append("graph.py does not mention broker_token_error")
    if not _has_getattr(source, "deps.client", "broker_token_error"):
        errors.append("graph.py _consult_ready is not getattr-safe for broker_token_error")
    if "session_stopped" in source and not _has_getattr(source, "deps.journal", "session_stopped"):
        errors.append("graph.py session_stopped is not getattr-safe")
    if "open_approvals" in source and not _has_getattr(source, "deps.journal", "open_approvals"):
        errors.append("graph.py open_approvals is not getattr-safe")
    if "live_symbols" in source and not (
        _has_getattr(source, "journal", "live_symbols")
        or _has_getattr(source, "deps.journal", "live_symbols")
    ):
        errors.append("graph.py live_symbols is not getattr-safe")
    return errors


def check_pkg(pkg: Path) -> list[str]:
    errors: list[str] = []
    trio = {
        "journal.py": check_journal,
        "openalgo_client.py": check_client,
        "graph.py": check_graph,
    }
    for name, checker in trio.items():
        path = pkg / name
        if not path.is_file():
            errors.append(f"missing {path}")
            continue
        source = path.read_text(encoding="utf-8")
        try:
            compile(source, str(path), "exec")
        except SyntaxError as exc:
            errors.append(f"{name} syntax error: {exc}")
            continue
        errors.extend(checker(source))
        print(f"{name} mtime={_mtime(path)} sha256={_sha256(path)} size={path.stat().st_size}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check-analyzer-plan-contract")
    parser.add_argument("--pkg", type=Path, default=DEFAULT_PKG)
    args = parser.parse_args(argv)
    pkg = args.pkg
    print("pkg", pkg)
    errors = check_pkg(pkg)
    if errors:
        for item in errors:
            print("FAIL", item, file=sys.stderr)
        return 2
    print("OK analyzer plan contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
