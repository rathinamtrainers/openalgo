import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.strategy_performance_narrate import _strategy_line, attach_narratives


def _report():
    return {
        "overall": {
            "summary": {
                "fills": 6,
                "closed_round_trips": 3,
                "wins": 2,
                "losses": 1,
                "realized_pnl": 15011.75,
                "unrealized_pnl": 0,
                "profit_factor": 2.16,
                "cagr_potential_pct": 6.9,
                "calendar_days": 22,
                "trading_days": 16,
            }
        },
        "strategies": [
            {
                "id": "analyzer-nifty",
                "name": "Strike Desk NIFTY Analyzer",
                "performance": {
                    "summary": {
                        "fills": 4,
                        "closed_round_trips": 2,
                        "wins": 1,
                        "losses": 1,
                        "realized_pnl": 12694.50,
                        "unrealized_pnl": 0,
                    }
                },
            },
            {
                "id": "coa-nifty",
                "name": "Strike Desk CoA NIFTY",
                "performance": {
                    "summary": {
                        "fills": 2,
                        "closed_round_trips": 1,
                        "wins": 1,
                        "losses": 0,
                        "realized_pnl": 1475.50,
                        "unrealized_pnl": 0,
                    }
                },
            },
            {
                "id": "ofa-nifty",
                "name": "Strike Desk OFA NIFTY",
                "performance": {
                    "summary": {
                        "fills": 0,
                        "closed_round_trips": 0,
                        "wins": 0,
                        "losses": 0,
                        "realized_pnl": 0,
                        "unrealized_pnl": 0,
                    }
                },
            },
        ],
    }


def test_template_uses_plain_words_and_report_figures():
    report = attach_narratives(_report(), invoke=None)
    narrative = report["narrative"]
    assert narrative["source"] == "template"
    overall = narrative["overall"]
    assert "Together, the strategies made +₹15,011.75" in overall
    assert "3 trades are finished (2 won, 1 lost)" in overall
    assert "Profit ratio 2.16" in overall
    assert "Over 22 calendar days (16 trading days)" in overall
    assert "Yearly pace +6.90%" in overall
    assert "Strike Desk NIFTY Analyzer made the most (+₹12,694.50)" in overall
    assert "fifo" not in overall.lower()
    assert "roi" not in overall.lower()
    by_id = {row["id"]: row["text"] for row in narrative["strategies"]}
    assert "made +₹12,694.50 from 2 finished trades (1 won, 1 lost)" in by_id["analyzer-nifty"]
    assert "ofa-nifty" not in by_id


def test_polish_keeps_template_when_rewrite_invents_a_number():
    report = attach_narratives(
        _report(),
        invoke=lambda *_: '{"overall": "They made +₹99,000.00.", "by_id": {}}',
    )
    assert report["narrative"]["source"] == "template"
    assert "+₹15,011.75" in report["narrative"]["overall"]


def test_polish_accepts_grounded_simple_rewrite():
    def _invoke(_system: str, _user: str) -> str:
        return (
            '{"overall": "All together they made +₹15,011.75. '
            '3 trades finished (2 won, 1 lost). '
            'Strike Desk NIFTY Analyzer made the most (+₹12,694.50).",'
            '"by_id": {'
            '"analyzer-nifty": "Strike Desk NIFTY Analyzer made +₹12,694.50 from 2 finished trades (1 won, 1 lost).",'
            '"coa-nifty": "Strike Desk CoA NIFTY made +₹1,475.50 from 1 finished trade (1 won, 0 lost).",'
            '"ofa-nifty": "Strike Desk OFA NIFTY has no trades yet."'
            "}}"
        )

    report = attach_narratives(_report(), invoke=_invoke)
    assert report["narrative"]["source"] == "llm"
    assert report["narrative"]["overall"].startswith("All together they made +₹15,011.75")


def test_idle_strategy_line():
    line = _strategy_line(
        "Strike Desk OFA NIFTY",
        {"fills": 0, "closed_round_trips": 0, "wins": 0, "losses": 0, "realized_pnl": 0},
    )
    assert line == "Strike Desk OFA NIFTY has no trades yet."


def test_strategy_line_skips_fantasy_yearly_pace_and_states_days():
    line = _strategy_line(
        "Strike Desk CoA NIFTY",
        {
            "fills": 2,
            "closed_round_trips": 2,
            "wins": 1,
            "losses": 1,
            "realized_pnl": 1475.50,
            "unrealized_pnl": 0,
            "profit_factor": 19.16,
            "cagr_potential_pct": 5161823038468.45,
            "calendar_days": 2,
            "trading_days": 2,
            "avg_profit_day": 1573.25,
            "avg_loss_day": -97.75,
        },
    )
    assert "Yearly pace" not in line
    assert "5161823038468" not in line
    assert "Over 2 calendar days (2 trading days)." in line
    assert "Average up day +₹1,573.25." in line
    assert "Average down day -₹97.75." in line
