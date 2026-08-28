"""The proposals command: one object rendered as text or JSON, and argument discipline."""

from __future__ import annotations

import json

import pytest

from strike_desk import __main__ as cli
from strike_desk.decline_taxonomy import TAXONOMY_ARTIFACT
from strike_desk.playbook import Playbook
from strike_desk.proposal_view import as_dict, render
from tests.chain_fixtures import SYMBOL, seed_proposal


def test_an_empty_window_says_so() -> None:
    text = render([], days=["2026-08-25"])
    assert "proposals        : 0 over 1 day(s)" in text
    assert TAXONOMY_ARTIFACT in text
    assert "(no proposal attempts in this window)" in text


def test_text_is_a_formatting_of_the_same_dict(journal, today) -> None:
    seed_proposal(journal, trading_day=today)
    row = journal.list_proposals(today)[0]
    view = as_dict(row)
    text = render([row], days=[today])

    contract = view["contract"]
    assert contract["symbol"] == SYMBOL
    assert SYMBOL in text
    assert f"x{contract['lots']} lot(s)" in text
    assert str(contract["delta"]) in text
    assert str(contract["implied_volatility"]) in text
    assert str(contract["open_interest"]) in text
    assert str(contract["breakeven"]) in text
    assert str(contract["stop_price"]) in text
    assert str(contract["target_price"]) in text
    assert contract["time_stop_ist"] in text
    assert view["playbook"]["verdict"] in text
    assert view["playbook"]["artifact"] in text
    assert f"${row.token_cost_micros / 1_000_000:.4f}" in text
    assert "PROPOSED" in text


def test_a_refusal_renders_without_inventing_a_contract(journal, today) -> None:
    seed_proposal(
        journal,
        trading_day=today,
        status="no-contract",
        symbol=None,
        expiry=None,
        strike=None,
        option_type=None,
        lots=None,
        lot_size=None,
        quantity=None,
        entry_price_low=None,
        entry_price_high=None,
        delta=None,
        theta_per_day=None,
        implied_volatility=None,
        open_interest=None,
        breakeven=None,
        stop_price=None,
        target_price=None,
        time_stop_ist=None,
        playbook_verdict="n/a",
        rationale="spread wider than 1.50%",
    )
    row = journal.list_proposals(today)[0]
    text = render([row], days=[today])
    assert "no contract" in text
    assert SYMBOL not in text
    assert "spread wider than 1.50%" in text


@pytest.fixture
def cli_settings(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def test_the_command_prints_text_and_json_from_the_same_rows(
    journal, today, cli_settings, capsys
) -> None:
    seed_proposal(journal, trading_day=today)
    assert cli.main(["proposals", "--day", today]) == 0
    text = capsys.readouterr().out
    assert cli.main(["proposals", "--day", today, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["taxonomy"] == TAXONOMY_ARTIFACT
    assert payload["playbook"] == Playbook.from_settings(cli_settings).artifact
    assert payload["days"] == [today]
    row = payload["proposals"][0]
    assert row["status"] == "proposed"
    assert row["contract"]["symbol"] == SYMBOL
    assert SYMBOL in text
    assert "PROPOSED" in text
    assert "pb-1+" in text


def test_the_command_rejects_a_malformed_day(cli_settings) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["proposals", "--day", "02-09-2026"])
    assert raised.value.code == 2


def test_the_command_rejects_a_zero_window(cli_settings) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["proposals", "--since", "0"])
    assert raised.value.code == 2


def test_the_command_rejects_a_day_and_a_window_together(cli_settings) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["proposals", "--day", "2026-09-02", "--since", "3"])
    assert raised.value.code == 2
