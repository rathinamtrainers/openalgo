"""Patch ZTA Nifty QuadShield (template 999043898): NetDelta, adj cap, delta wings.

Quiet by design: no print/logging inside Tradetron python_code; AST-only edits.
"""

from __future__ import annotations

import json
from pathlib import Path

SRC = Path(
    r"C:\Users\Admin\.cursor\projects\d-ATZ-zerotouchalgo-RT-openalgo"
    r"\agent-tools\6d586985-dfe6-403e-a7c8-58472ff27c71.txt"
)
OUT = Path(__file__).resolve().parents[1] / ".state-manual" / "quadshield_nifty_patched.json"
PATCH_OUT = Path(__file__).resolve().parents[1] / ".state-manual" / "quadshield_nifty_patch.json"


def find_strike_json(opt: str, delta: str) -> str:
    tree = {
        "operator": "and",
        "operands": [
            {
                "type": "rule",
                "value": "builder-basic_rule_0",
                "elements": [
                    {
                        "name": "Find Strike",
                        "kid": 5110,
                        "params": [
                            {"type": "value", "value": "NIFTY 50"},
                            {
                                "type": "keyword",
                                "keyword": {
                                    "name": "Select Expiry",
                                    "kid": 3242,
                                    "params": [
                                        {"type": "value", "value": "week"},
                                        {"type": "value", "value": "0"},
                                    ],
                                },
                            },
                            {"type": "value", "value": "delta"},
                            {
                                "type": "keyword",
                                "keyword": {
                                    "name": "Number",
                                    "kid": 2275,
                                    "params": [{"type": "value", "value": delta}],
                                },
                            },
                            {"type": "value", "value": opt},
                            {"type": "value", "value": "any"},
                        ],
                    }
                ],
            }
        ],
    }
    return json.dumps(tree, separators=(",", ":"))


def expiry_week0_json() -> str:
    tree = {
        "operator": "and",
        "operands": [
            {
                "type": "rule",
                "value": "builder-basic_rule_0",
                "elements": [
                    {
                        "name": "Current Week Expiry",
                        "kid": 3015,
                        "params": [
                            {"type": "value", "value": "NIFTY 50"},
                            {"type": "value", "value": "0"},
                        ],
                    }
                ],
            }
        ],
    }
    return json.dumps(tree, separators=(",", ":"))


def number_json(n: str) -> str:
    return json.dumps(
        {
            "operator": "and",
            "operands": [
                {
                    "type": "rule",
                    "value": "builder-basic_rule_0",
                    "elements": [
                        {
                            "name": "Number",
                            "kid": 8603,
                            "params": [{"type": "value", "value": n}],
                        }
                    ],
                }
            ],
        },
        separators=(",", ":"),
    )


def count_inc_json() -> str:
    return json.dumps(
        {
            "operator": "and",
            "operands": [
                {
                    "type": "rule",
                    "value": "builder-basic_rule_0",
                    "elements": [
                        {
                            "name": "Get Runtime Number",
                            "kid": 7523,
                            "params": [
                                {"type": "value", "value": "CountOfAdjustmentsSoFar"}
                            ],
                        },
                        {"name": "+", "params": []},
                        {
                            "name": "Number",
                            "kid": 1917,
                            "params": [{"type": "value", "value": "1"}],
                        },
                    ],
                }
            ],
        },
        separators=(",", ":"),
    )


def _has_net_delta(node: object) -> bool:
    return "Net Delta" in json.dumps(node)


def patch_strategy(s: dict) -> dict:
    wings = s["sets"][0]["conditions"][0]
    assert wings["type"] == "Entry"
    for leg, delta in zip(wings["legs"], ("0.10", "-0.10"), strict=True):
        opt = leg.get("option_type") or leg.get("optionType")
        leg["strike_json"] = find_strike_json(str(opt), delta)
        leg["expiry_json"] = expiry_week0_json()
        leg["strike"] = None
        leg["strike_display"] = None
        leg["expiry"] = None
        leg["expiry_display"] = None

    vars0 = wings.setdefault("extra", {}).setdefault("variables", [])
    found = False
    for v in vars0:
        if v.get("variable_name") == "CountOfAdjustmentsSoFar":
            v["value"] = "( tt_Number ( '0' )  )"
            v["display"] = "( Number ( '0' )  )"
            v["json"] = number_json("0")
            found = True
    if not found:
        vars0.append(
            {
                "variable_name": "CountOfAdjustmentsSoFar",
                "value": "( tt_Number ( '0' )  )",
                "display": "( Number ( '0' )  )",
                "json": number_json("0"),
            }
        )

    entry2 = s["sets"][1]["conditions"][0]
    assert entry2["type"] == "Entry"
    ej = json.loads(entry2["condition_json"])
    already = any(
        isinstance(o, dict)
        and "CountOfAdjustmentsSoFar" in json.dumps(o)
        and "<" in json.dumps(o)
        for o in ej["operands"]
    )
    if not already:
        ej["operands"].append(
            {
                "type": "rule",
                "value": "builder-basic_rule_adjcap",
                "elements": [
                    {
                        "name": "Get Runtime Number",
                        "kid": 9483,
                        "params": [
                            {"type": "value", "value": "CountOfAdjustmentsSoFar"}
                        ],
                    },
                    {"name": "<", "params": []},
                    {
                        "name": "Number",
                        "kid": 8283,
                        "params": [{"type": "value", "value": "1"}],
                    },
                ],
            }
        )
    entry2["condition_json"] = json.dumps(ej, separators=(",", ":"))
    entry2["condition_value"] = None
    entry2["condition_display"] = None

    exit2 = s["sets"][1]["conditions"][2]
    assert exit2["type"] == "Exit"
    xj = json.loads(exit2["condition_json"])
    new_nd = {
        "type": "rule",
        "value": "builder-basic_rule_1",
        "elements": [
            {
                "name": "Absolute",
                "kid": 3692,
                "params": [
                    {
                        "type": "keyword",
                        "keyword": {
                            "name": "Net Delta",
                            "kid": 9209,
                            "params": [{"type": "value", "value": "NIFTY 50"}],
                        },
                    }
                ],
            },
            {"name": "/", "params": []},
            {
                "name": "Number",
                "kid": 6526,
                "params": [{"type": "value", "value": "65"}],
            },
            {"name": ">", "params": []},
            {
                "name": "Number",
                "kid": 6527,
                "params": [{"type": "value", "value": "0.40"}],
            },
            {"name": "*", "params": []},
            {"name": "Multiplier", "params": []},
        ],
    }
    replaced = False
    for i, o in enumerate(xj["operands"]):
        if _has_net_delta(o):
            xj["operands"][i] = new_nd
            replaced = True
            break
    if not replaced:
        raise SystemExit("Net Delta operand not found in Set2 Exit")
    exit2["condition_json"] = json.dumps(xj, separators=(",", ":"))
    exit2["condition_value"] = None
    exit2["condition_display"] = None

    for v in exit2.setdefault("extra", {}).setdefault("variables", []):
        if v.get("variable_name") == "CountOfAdjustmentsSoFar":
            v["value"] = (
                "( get_runtime ( 'strategy_id', 'run_counter', "
                "'CountOfAdjustmentsSoFar' ) + tt_Number ( '1' )  )"
            )
            v["display"] = (
                "( Get Runtime Number ( 'CountOfAdjustmentsSoFar' ) "
                "+ Number ( '1' )  )"
            )
            v["json"] = count_inc_json()

    ue = s["sets"][2]["conditions"][2]
    assert ue["type"] == "Universal Exit"
    uevars = ue.setdefault("extra", {}).setdefault("variables", [])
    ue_found = False
    for v in uevars:
        if v.get("variable_name") == "CountOfAdjustmentsSoFar":
            v["value"] = "( tt_Number ( '0' )  )"
            v["display"] = "( Number ( '0' )  )"
            v["json"] = number_json("0")
            ue_found = True
    if not ue_found:
        uevars.append(
            {
                "variable_name": "CountOfAdjustmentsSoFar",
                "value": "( tt_Number ( '0' )  )",
                "display": "( Number ( '0' )  )",
                "json": number_json("0"),
            }
        )

    # No strategy python_code — keeps engine logs quiet.
    s["pythonCode"] = None
    s["python_code"] = None
    return s


def build_patch(original: dict, patched: dict) -> list[dict]:
    """Minimal RFC6902 ops for tt_apply_strategy_patch (json fields only)."""
    ops: list[dict] = []
    # wings legs strike/expiry json
    for li in (0, 1):
        ops.append(
            {
                "op": "replace",
                "path": f"/sets/0/conditions/0/legs/{li}/strike_json",
                "value": patched["sets"][0]["conditions"][0]["legs"][li]["strike_json"],
            }
        )
        ops.append(
            {
                "op": "replace",
                "path": f"/sets/0/conditions/0/legs/{li}/expiry_json",
                "value": patched["sets"][0]["conditions"][0]["legs"][li]["expiry_json"],
            }
        )
    ops.append(
        {
            "op": "replace",
            "path": "/sets/0/conditions/0/extra",
            "value": patched["sets"][0]["conditions"][0]["extra"],
        }
    )
    ops.append(
        {
            "op": "replace",
            "path": "/sets/1/conditions/0/condition_json",
            "value": patched["sets"][1]["conditions"][0]["condition_json"],
        }
    )
    ops.append(
        {
            "op": "replace",
            "path": "/sets/1/conditions/2/condition_json",
            "value": patched["sets"][1]["conditions"][2]["condition_json"],
        }
    )
    ops.append(
        {
            "op": "replace",
            "path": "/sets/1/conditions/2/extra",
            "value": patched["sets"][1]["conditions"][2]["extra"],
        }
    )
    ops.append(
        {
            "op": "replace",
            "path": "/sets/2/conditions/2/extra",
            "value": patched["sets"][2]["conditions"][2]["extra"],
        }
    )
    del original  # unused; kept for signature clarity
    return ops


def main() -> None:
    raw = json.loads(SRC.read_text(encoding="utf-8"))
    original = raw["data"]["strategy"]
    patched = patch_strategy(json.loads(json.dumps(original)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(patched), encoding="utf-8")
    patch = build_patch(original, patched)
    PATCH_OUT.write_text(json.dumps(patch, indent=2), encoding="utf-8")
    print(f"strategy={OUT}")
    print(f"patch_ops={len(patch)} -> {PATCH_OUT}")
    xj = json.loads(patched["sets"][1]["conditions"][2]["condition_json"])
    print("nd_fix", [e.get("name") for e in xj["operands"][1]["elements"]])
    print(
        "entry_ops",
        len(json.loads(patched["sets"][1]["conditions"][0]["condition_json"])["operands"]),
    )


if __name__ == "__main__":
    main()
