"""Text and JSON are two renderings of one row, so they cannot disagree."""

from __future__ import annotations

import json

import pytest

from strike_desk import __main__ as cli
from strike_desk.risk_officer import RiskLimits
from strike_desk.risk_view import as_dict, render

from .risk_fixtures import ROOMY, seed_verdict


def test_the_json_holds_every_number_the_text_prints(journal) -> None:
    seed_verdict(
        journal,
        trading_day="2026-09-01",
        verdict="reduce",
        lots_requested=2,
        lots_cleared=1,
        tripped_limit="per-trade-loss-cap",
        configured_value=4_000.0,
        observed_value=6_000.0,
        limit_unit="INR",
        checks_json=json.dumps(
            [
                {
                    "limit": "per-trade-loss-cap",
                    "unit": "INR",
                    "configured": 4_000.0,
                    "observed": 6_000.0,
                    "breached": True,
                    "detail": "2 lot(s)",
                }
            ]
        ),
    )
    rows = journal.list_risk_verdicts("2026-09-01")
    text = render(list(rows), days=["2026-09-01"])
    view = as_dict(rows[0])

    assert "REDUCE" in text and "2 -> 1 lot(s)" in text
    assert "per-trade-loss-cap" in text
    assert "Rs 4,000" in text and "Rs 6,000" in text
    assert view["tripped"] == {
        "limit": "per-trade-loss-cap",
        "configured": 4_000.0,
        "observed": 6_000.0,
        "unit": "INR",
    }
    assert view["lots"] == {"requested": 2, "cleared": 1}
    assert view["capital_base"] == ROOMY
    assert json.dumps(view, default=str)  # serialisable as --json prints it


def test_an_empty_window_says_so(journal) -> None:
    assert "no adjudications" in render([], days=[])


@pytest.fixture
def cli_settings(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def test_the_command_prints_text_and_json_from_the_same_rows(
    journal, today, cli_settings, capsys
) -> None:
    seed_verdict(journal, trading_day=today, verdict="pass")
    assert cli.main(["risk", "--day", today]) == 0
    text = capsys.readouterr().out
    assert cli.main(["risk", "--day", today, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["limits"] == RiskLimits.from_settings(cli_settings).artifact
    assert payload["days"] == [today]
    assert payload["verdicts"][0]["verdict"] == "pass"
    assert "PASS" in text
    assert "rl-1+" in text


def test_the_command_rejects_a_malformed_day(cli_settings) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["risk", "--day", "02-09-2026"])
    assert raised.value.code == 2


def test_the_command_rejects_a_zero_window(cli_settings) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["risk", "--since", "0"])
    assert raised.value.code == 2
